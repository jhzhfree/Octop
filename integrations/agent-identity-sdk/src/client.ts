import { bytes, canonicalOrigin, createProofIssuer, IdentityError, sha256, type Invocation } from "./core.js";
import type { AgentBinding, BoundRequestProofIssuer, CredentialSource, JsonValue, KnowledgeRequest, RsaSigner } from "./types.js";

export function validateRequest(request: KnowledgeRequest): KnowledgeRequest {
  if (!request || !["bases", "search", "query"].includes(request.operation)) throw new IdentityError("PROTOCOL_UNSUPPORTED", "Unknown operation");
  if (request.operation === "bases") return Object.freeze({ operation: "bases" });
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
  readonly origin: string;
  constructor(private readonly credentials: CredentialSource, origin: string, private readonly transport: "mcp" | "rest",
    private readonly timeoutMs = 15000, insecureLoopback = false) {
    this.origin = canonicalOrigin(origin, insecureLoopback);
    if (!["mcp", "rest"].includes(transport) || !Number.isInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 60000) throw new IdentityError("PROTOCOL_UNSUPPORTED", "Invalid transport settings");
  }
  private async request(proofs: BoundRequestProofIssuer, method: "GET" | "POST", target: string, payload: unknown, signal: AbortSignal): Promise<any> {
    signal.throwIfAborted();
    const body = payload === undefined ? new Uint8Array() : bytes(JSON.stringify(payload));
    const serviceKey = await this.credentials.readServiceKey(signal);
    if (!/^[A-Za-z0-9_-]+$/.test(serviceKey)) throw new IdentityError("AUTHENTICATION_FAILED", "Invalid service credential value");
    const proof = await proofs.issue({ method, target, body }, await sha256(bytes(serviceKey)), signal);
    signal.throwIfAborted();
    let response: Response;
    try {
      response = await fetch(this.origin + target, { method, redirect: "error", body: method === "GET" ? undefined : new Uint8Array(body),
        headers: { "X-API-Key": serviceKey, [proof.headerName]: proof.value, "Content-Type": "application/json", Accept: "application/json" },
        signal: AbortSignal.any([signal, AbortSignal.timeout(this.timeoutMs)]) });
    } catch {
      signal.throwIfAborted();
      throw new IdentityError("UPSTREAM_UNAVAILABLE", "AKB network request failed", true);
    }
    const chunks: Uint8Array[] = []; let size = 0;
    const reader = response.body?.getReader();
    try {
      if (reader) while (true) {
        const chunk = await reader.read(); if (chunk.done) break;
        size += chunk.value.byteLength;
        if (size > 2_000_000) throw new IdentityError("UPSTREAM_UNAVAILABLE", "AKB response exceeds size limit");
        chunks.push(chunk.value);
      }
    } finally { await reader?.cancel(); }
    if (!response.ok) {
      const code = response.status === 401 ? "AUTHENTICATION_FAILED" : response.status === 403 ? "AUTHORIZATION_DENIED"
        : response.status === 429 ? "RATE_LIMITED" : "UPSTREAM_UNAVAILABLE";
      throw new IdentityError(code, `AKB request rejected (HTTP ${response.status})`, response.status === 429 || response.status >= 500);
    }
    const raw = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { raw.set(chunk, offset); offset += chunk.length; }
    try { return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw)); }
    catch { throw new IdentityError("UPSTREAM_UNAVAILABLE", "Invalid AKB JSON response"); }
  }
  async status(binding: AgentBinding, invocation: Invocation, signer: RsaSigner, signal: AbortSignal) {
    if (binding.resource !== this.origin) throw new IdentityError("PROTOCOL_UNSUPPORTED", "Binding resource does not match AKB origin");
    const result = await this.request(createProofIssuer(binding, invocation, signer), "GET", "/api/v1/external/identity/status", undefined, signal);
    if (result.success !== true || result.data?.agentId !== binding.externalAgentId || result.data?.kid !== signer.kid
      || result.data?.proofProfile !== "AGENT_KEY_RS256_V2" || result.data?.keyStatus !== "ACTIVE" || String(result.data?.policyId) !== binding.policyId) {
      throw new IdentityError("KEY_NOT_REGISTERED", "AKB identity probe did not confirm the expected key owner");
    }
    return result.data;
  }
  async execute(request: KnowledgeRequest, binding: AgentBinding, invocation: Invocation, signer: RsaSigner, signal: AbortSignal): Promise<JsonValue> {
    const input = validateRequest(request);
    if (binding.resource !== this.origin) throw new IdentityError("PROTOCOL_UNSUPPORTED", "Binding resource does not match AKB origin");
    const proofs = createProofIssuer(binding, invocation, signer);
    if (this.transport === "mcp") {
      const rpc = async (method: string, params: unknown) => {
        const id = crypto.randomUUID();
        const result = await this.request(proofs, "POST", "/api/mcp", { jsonrpc: "2.0", id, method, params }, signal);
        if (result.jsonrpc !== "2.0" || result.id !== id || result.error || !result.result || result.result.isError) throw new IdentityError("AUTHORIZATION_DENIED", "AKB rejected the MCP operation");
        return result.result;
      };
      const init = await rpc("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "akb-agent-identity", version: "0.2.0" } });
      if (init.protocolVersion !== "2025-06-18") throw new IdentityError("PROTOCOL_UNSUPPORTED", "Unsupported AKB MCP protocol");
      const name = input.operation === "bases" ? "list_knowledge_bases" : input.operation === "search" ? "knowledge_search" : "knowledge_query";
      const args = input.operation === "bases" ? {} : { topK: input.topK, knowledgeBaseId: input.knowledgeBaseId,
        knowledgeBaseCode: input.knowledgeBaseCode, [input.operation === "search" ? "query" : "question"]: input.query };
      return rpc("tools/call", { name, arguments: args });
    }
    if (input.operation === "bases") return this.restResult(await this.request(proofs, "GET", "/api/v1/external/bases", undefined, signal));
    let knowledgeBaseId = input.knowledgeBaseId;
    if (input.knowledgeBaseCode) {
      const bases = this.restResult(await this.request(proofs, "GET", "/api/v1/external/bases", undefined, signal)) as any;
      if (!Array.isArray(bases)) throw new IdentityError("UPSTREAM_UNAVAILABLE", "Invalid AKB base discovery response");
      const match = bases.find((b: any) => b.baseCode === input.knowledgeBaseCode);
      if (!match || !Number.isSafeInteger(match.id) || (knowledgeBaseId !== undefined && match.id !== knowledgeBaseId)) throw new IdentityError("AUTHORIZATION_DENIED", "Unknown, unauthorized or mismatched AKB base reference");
      knowledgeBaseId = match.id;
    }
    return this.restResult(await this.request(proofs, "POST", `/api/v1/external/${input.operation}`,
      { knowledgeBaseId, question: input.query, topK: input.topK }, signal));
  }
  private restResult(result: any): JsonValue {
    if (result?.success !== true || !("data" in result)) throw new IdentityError("UPSTREAM_UNAVAILABLE", "AKB rejected the REST operation");
    return result.data;
  }
}
