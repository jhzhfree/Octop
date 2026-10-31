export class IdentityError extends Error {
    code;
    retryable;
    constructor(code, message, retryable = false) {
        super(message);
        this.code = code;
        this.retryable = retryable;
        this.name = "IdentityError";
    }
    get safeMessage() { return this.message; }
}
export const identifier = (v) => typeof v === "string" && /^[A-Za-z0-9_.:-]{1,128}$/.test(v);
export const uuid = (v) => typeof v === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
export const traceId = (v) => typeof v === "string" && v.length > 0 && v.length <= 128 && !/[\x00-\x1f\x7f]/.test(v);
export const bytes = (v) => new TextEncoder().encode(v);
export function base64url(v) {
    let binary = "";
    for (const byte of v)
        binary += String.fromCharCode(byte);
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
export async function sha256(v) {
    return [...new Uint8Array(await crypto.subtle.digest("SHA-256", new Uint8Array(v)))].map(b => b.toString(16).padStart(2, "0")).join("");
}
export async function publicKid(jwk) {
    return base64url(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes(JSON.stringify({ e: jwk.e, kty: jwk.kty, n: jwk.n })))));
}
export function ownerIndex(owner) {
    if (!identifier(owner.platformId) || !identifier(owner.installationId) || !identifier(owner.workspaceId) || !uuid(owner.externalAgentId)) {
        throw new IdentityError("AGENT_UNREGISTERED", "Invalid Agent key owner");
    }
    return JSON.stringify([owner.platformId, owner.installationId, owner.workspaceId, owner.externalAgentId]);
}
export function ownerOf(binding) {
    return Object.freeze({ platformId: binding.platformId, installationId: binding.installationId, workspaceId: binding.workspaceId, externalAgentId: binding.externalAgentId });
}
/** RFC1918 (plus link-local) literal IPv4 ranges, for private-network HTTP. */
const PRIVATE_IPV4 = [/^10\./, /^192\.168\./, /^169\.254\./, /^172\.(1[6-9]|2\d|3[01])\./];
export function isPrivateHost(hostname) {
    const host = hostname.toLowerCase().replace(/^\[|\]$/g, "");
    if (["localhost", "127.0.0.1", "::1"].includes(host))
        return true;
    if (/^(fc|fd|fe80)/.test(host))
        return true; // IPv6 unique-local / link-local
    return /^\d+\.\d+\.\d+\.\d+$/.test(host) && PRIVATE_IPV4.some(range => range.test(host));
}
export function canonicalOrigin(value, insecureLoopback = false, allowPrivateHttp = false) {
    const url = new URL(value);
    const loopback = insecureLoopback && url.protocol === "http:" && ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname);
    // Deployments inside a trusted LAN often cannot terminate TLS at the AKB
    // host; that stays opt-in and is limited to private address space.
    const privateHttp = allowPrivateHttp && url.protocol === "http:" && isPrivateHost(url.hostname);
    if ((url.protocol !== "https:" && !loopback && !privateHttp) || url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
        throw new IdentityError("PROTOCOL_UNSUPPORTED", allowPrivateHttp
            ? "AKB origin must be HTTPS, an explicit loopback override, or a private-network host"
            : "AKB origin must be HTTPS (explicit loopback development override only)");
    }
    return url.origin;
}
export async function signJws(claims, signer, type, signal) {
    signal.throwIfAborted();
    const input = `${base64url(bytes(JSON.stringify({ alg: "RS256", typ: type, kid: signer.kid })))}.${base64url(bytes(JSON.stringify(claims)))}`;
    const signature = await signer.sign(bytes(input), signal);
    signal.throwIfAborted();
    const result = `${input}.${base64url(signature)}`;
    if (result.length > 8192)
        throw new IdentityError("PROTOCOL_UNSUPPORTED", "Agent proof exceeds size limit");
    return result;
}
export function createProofIssuer(binding, invocation, signer) {
    const approved = Object.freeze({ ...binding }), context = Object.freeze({ ...invocation });
    return {
        async issue(request, credentialSha256, signal) {
            if (!/^[0-9a-f]{64}$/.test(credentialSha256) || !request.target.startsWith("/") || request.target.startsWith("//")
                || request.target.length > 2048 || request.body.length > 1048576 || !["GET", "POST"].includes(request.method)) {
                throw new IdentityError("PROTOCOL_UNSUPPORTED", "Invalid request proof input");
            }
            if (context.terminalAgentName !== undefined && !identifier(context.terminalAgentName))
                throw new IdentityError("AGENT_UNREGISTERED", "Invalid terminal Agent name");
            const now = Math.floor(Date.now() / 1000), jti = crypto.randomUUID();
            const claims = {
                ver: 2, iss: approved.issuer, aud: approved.audience, resource: approved.resource, policy_id: approved.policyId,
                azp: approved.applicationPrincipalId, sub: `agent:${approved.externalAgentId}`, platform_id: approved.platformId,
                installation_id: approved.installationId, workspace_id: approved.workspaceId, runtime_agent: approved.runtimeAgentKey,
                terminal_agent: context.terminalAgentName, session_id: context.sessionId, message_id: context.messageId, invocation_id: context.invocationId,
                iat: now, exp: now + 60, jti, credential_sha256: credentialSha256,
                request_binding: { method: request.method, target: request.target, body_sha256: await sha256(request.body) },
            };
            return { headerName: "X-AKB-Workload-Assertion", value: await signJws(claims, signer, "akb-agent-proof+jwt", signal), kid: signer.kid, jti, expiresAt: now + 60 };
        },
    };
}
