import { bytes, canonicalOrigin, createProofIssuer, IdentityError, sha256, uuid, identifier } from "./core.js";
export function validateRequest(request) {
    if (!request || !["bases", "search", "query"].includes(request.operation))
        throw new IdentityError("PROTOCOL_UNSUPPORTED", "Unknown operation");
    if (request.operation === "bases")
        return Object.freeze({ operation: "bases" });
    if (typeof request.query !== "string" || request.query.length < 1 || request.query.length > 4000
        || (request.topK !== undefined && (!Number.isInteger(request.topK) || request.topK < 1 || request.topK > 50))
        || (request.knowledgeBaseId !== undefined && (!Number.isSafeInteger(request.knowledgeBaseId) || request.knowledgeBaseId <= 0))
        || (request.knowledgeBaseCode !== undefined && (typeof request.knowledgeBaseCode !== "string" || request.knowledgeBaseCode.length < 1 || request.knowledgeBaseCode.length > 128))) {
        throw new IdentityError("PROTOCOL_UNSUPPORTED", "Invalid knowledge request");
    }
    return Object.freeze({ operation: request.operation, query: request.query, topK: request.topK,
        knowledgeBaseId: request.knowledgeBaseId, knowledgeBaseCode: request.knowledgeBaseCode });
}
export class AkbKnowledgeClient {
    credentials;
    transport;
    timeoutMs;
    origin;
    constructor(credentials, origin, transport, timeoutMs = 15000, insecureLoopback = false) {
        this.credentials = credentials;
        this.transport = transport;
        this.timeoutMs = timeoutMs;
        this.origin = canonicalOrigin(origin, insecureLoopback);
        if (!["mcp", "rest"].includes(transport) || !Number.isInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 60000)
            throw new IdentityError("PROTOCOL_UNSUPPORTED", "Invalid transport settings");
    }
    async request(proofs, method, target, payload, signal) {
        signal.throwIfAborted();
        const body = payload === undefined ? new Uint8Array() : bytes(JSON.stringify(payload));
        const serviceKey = await this.credentials.readServiceKey(signal);
        if (!/^[A-Za-z0-9_-]+$/.test(serviceKey))
            throw new IdentityError("AUTHENTICATION_FAILED", "Invalid service credential value");
        const proof = await proofs.issue({ method, target, body }, await sha256(bytes(serviceKey)), signal);
        signal.throwIfAborted();
        let response;
        try {
            response = await fetch(this.origin + target, { method, redirect: "error", body: method === "GET" ? undefined : new Uint8Array(body),
                headers: { "X-API-Key": serviceKey, [proof.headerName]: proof.value, "Content-Type": "application/json", Accept: "application/json" },
                signal: AbortSignal.any([signal, AbortSignal.timeout(this.timeoutMs)]) });
        }
        catch {
            signal.throwIfAborted();
            throw new IdentityError("UPSTREAM_UNAVAILABLE", "AKB network request failed", true);
        }
        const chunks = [];
        let size = 0;
        const reader = response.body?.getReader();
        try {
            if (reader)
                while (true) {
                    const chunk = await reader.read();
                    if (chunk.done)
                        break;
                    size += chunk.value.byteLength;
                    if (size > 2_000_000)
                        throw new IdentityError("UPSTREAM_UNAVAILABLE", "AKB response exceeds size limit");
                    chunks.push(chunk.value);
                }
        }
        finally {
            await reader?.cancel();
        }
        if (!response.ok) {
            const code = response.status === 401 ? "AUTHENTICATION_FAILED" : response.status === 403 ? "AUTHORIZATION_DENIED"
                : response.status === 429 ? "RATE_LIMITED" : "UPSTREAM_UNAVAILABLE";
            throw new IdentityError(code, `AKB request rejected (HTTP ${response.status})`, response.status === 429 || response.status >= 500);
        }
        const raw = new Uint8Array(size);
        let offset = 0;
        for (const chunk of chunks) {
            raw.set(chunk, offset);
            offset += chunk.length;
        }
        try {
            return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw));
        }
        catch {
            throw new IdentityError("UPSTREAM_UNAVAILABLE", "Invalid AKB JSON response");
        }
    }
    async configuration(agentId, signal) {
        if (!uuid(agentId))
            throw new IdentityError("AGENT_UNREGISTERED", "Agent ID must be a UUID");
        const key = await this.credentials.readServiceKey(signal);
        if (!/^[A-Za-z0-9_-]+$/.test(key))
            throw new IdentityError("AUTHENTICATION_FAILED", "Invalid service credential");
        let result;
        try {
            const response = await fetch(this.origin + "/api/v1/external/identity/configuration?agentId=" + encodeURIComponent(agentId), {
                headers: { "X-API-Key": key, Accept: "application/json" }, redirect: "error", signal: AbortSignal.any([signal, AbortSignal.timeout(this.timeoutMs)])
            });
            if (!response.ok)
                throw new IdentityError(response.status === 401 ? "AUTHENTICATION_FAILED" : "KEY_NOT_REGISTERED", "AKB connection or Agent registration is not ready");
            const reader = response.body?.getReader();
            const chunks = [];
            let size = 0;
            try {
                if (reader)
                    while (true) {
                        const c = await reader.read();
                        if (c.done)
                            break;
                        size += c.value.length;
                        if (size > 16384)
                            throw new IdentityError("PROTOCOL_UNSUPPORTED", "Invalid connection metadata");
                        chunks.push(c.value);
                    }
            }
            finally {
                await reader?.cancel();
            }
            const bytes = new Uint8Array(size);
            let offset = 0;
            for (const c of chunks) {
                bytes.set(c, offset);
                offset += c.length;
            }
            result = JSON.parse(new TextDecoder().decode(bytes));
        }
        catch (e) {
            if (e instanceof IdentityError)
                throw e;
            throw new IdentityError("UPSTREAM_UNAVAILABLE", "AKB connection discovery failed", true);
        }
        const b = result?.data;
        if (result?.success !== true || !b || b.resource !== this.origin || b.externalAgentId !== agentId || !identifier(b.runtimeAgentKey)
            || !["platformId", "installationId", "workspaceId"].every(k => identifier(b[k]))
            || !["policyId", "applicationPrincipalId", "bindingId"].every(k => typeof b[k] === "string" && /^[1-9][0-9]{0,18}$/.test(b[k]))
            || typeof b.issuer !== "string" || !b.issuer.startsWith("https://") || typeof b.audience !== "string" || !b.audience.length || b.audience.length > 128) {
            throw new IdentityError("PROTOCOL_UNSUPPORTED", "AKB returned mismatched connection metadata");
        }
        return Object.freeze(Object.fromEntries(["platformId", "installationId", "workspaceId", "externalAgentId", "runtimeAgentKey", "policyId", "applicationPrincipalId", "bindingId", "issuer", "audience", "resource"].map(k => [k, b[k]])));
    }
    async status(binding, invocation, signer, signal) {
        if (binding.resource !== this.origin)
            throw new IdentityError("PROTOCOL_UNSUPPORTED", "Binding resource does not match AKB origin");
        const result = await this.request(createProofIssuer(binding, invocation, signer), "GET", "/api/v1/external/identity/status", undefined, signal);
        if (result.success !== true || result.data?.agentId !== binding.externalAgentId || result.data?.kid !== signer.kid
            || result.data?.proofProfile !== "AGENT_KEY_RS256_V2" || result.data?.keyStatus !== "ACTIVE" || String(result.data?.policyId) !== binding.policyId) {
            throw new IdentityError("KEY_NOT_REGISTERED", "AKB identity probe did not confirm the expected key owner");
        }
        return result.data;
    }
    async execute(request, binding, invocation, signer, signal) {
        const input = validateRequest(request);
        if (binding.resource !== this.origin)
            throw new IdentityError("PROTOCOL_UNSUPPORTED", "Binding resource does not match AKB origin");
        const proofs = createProofIssuer(binding, invocation, signer);
        if (this.transport === "mcp") {
            const rpc = async (method, params) => {
                const id = crypto.randomUUID();
                const result = await this.request(proofs, "POST", "/api/mcp", { jsonrpc: "2.0", id, method, params }, signal);
                if (result.jsonrpc !== "2.0" || result.id !== id || result.error || !result.result || result.result.isError)
                    throw new IdentityError("AUTHORIZATION_DENIED", "AKB rejected the MCP operation");
                return result.result;
            };
            const init = await rpc("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "akb-agent-identity", version: "0.2.0" } });
            if (init.protocolVersion !== "2025-06-18")
                throw new IdentityError("PROTOCOL_UNSUPPORTED", "Unsupported AKB MCP protocol");
            const name = input.operation === "bases" ? "list_knowledge_bases" : input.operation === "search" ? "knowledge_search" : "knowledge_query";
            const args = input.operation === "bases" ? {} : { topK: input.topK, knowledgeBaseId: input.knowledgeBaseId,
                knowledgeBaseCode: input.knowledgeBaseCode, [input.operation === "search" ? "query" : "question"]: input.query };
            return rpc("tools/call", { name, arguments: args });
        }
        if (input.operation === "bases")
            return this.restResult(await this.request(proofs, "GET", "/api/v1/external/bases", undefined, signal));
        let knowledgeBaseId = input.knowledgeBaseId;
        if (input.knowledgeBaseCode) {
            const bases = this.restResult(await this.request(proofs, "GET", "/api/v1/external/bases", undefined, signal));
            if (!Array.isArray(bases))
                throw new IdentityError("UPSTREAM_UNAVAILABLE", "Invalid AKB base discovery response");
            const match = bases.find((b) => b.baseCode === input.knowledgeBaseCode);
            if (!match || !Number.isSafeInteger(match.id) || (knowledgeBaseId !== undefined && match.id !== knowledgeBaseId))
                throw new IdentityError("AUTHORIZATION_DENIED", "Unknown, unauthorized or mismatched AKB base reference");
            knowledgeBaseId = match.id;
        }
        return this.restResult(await this.request(proofs, "POST", `/api/v1/external/${input.operation}`, { knowledgeBaseId, question: input.query, topK: input.topK }, signal));
    }
    restResult(result) {
        if (result?.success !== true || !("data" in result))
            throw new IdentityError("UPSTREAM_UNAVAILABLE", "AKB rejected the REST operation");
        return result.data;
    }
}
