/** Public platform-independent SDK contracts. */
export type JsonValue = null | boolean | number | string | readonly JsonValue[] | {
    readonly [key: string]: JsonValue;
};
export type Operation = "bases" | "search" | "query";
export type KnowledgeRequest = Readonly<{
    operation: "bases";
}> | Readonly<{
    operation: "search" | "query";
    query: string;
    topK?: number;
    knowledgeBaseId?: number;
    knowledgeBaseCode?: string;
}>;
export interface HostExecution {
    readonly platformId: string;
    readonly runtimeAgentKey: string;
    readonly sessionId: string;
    readonly messageId?: string;
    readonly workspaceRoot: string;
    readonly abortSignal: AbortSignal;
}
export interface ApprovalSummary {
    readonly operation: Operation;
    readonly knowledgeBaseId?: number;
    readonly knowledgeBaseCode?: string;
}
export interface CapturedExecution {
    readonly context: HostExecution;
    authorize(summary: ApprovalSummary): Promise<void>;
}
export interface PlatformAdapter<NativeContext> {
    /** Synchronous: capture host execution identity and approval runtime before any await. */
    capture(native: NativeContext): CapturedExecution;
}
export interface AgentKeyOwner {
    readonly platformId: string;
    readonly installationId: string;
    readonly workspaceId: string;
    readonly externalAgentId: string;
}
export interface AgentBinding extends AgentKeyOwner {
    readonly policyId: string;
    readonly bindingId: string;
    readonly runtimeAgentKey: string;
    readonly applicationPrincipalId: string;
    readonly issuer: string;
    readonly audience: string;
    readonly resource: string;
}
export interface RsaPublicJwk {
    readonly kty: "RSA";
    readonly n: string;
    readonly e: string;
    readonly alg: "RS256";
    readonly use: "sig";
    readonly kid: string;
}
export interface PublicAgentKey {
    readonly owner: AgentKeyOwner;
    readonly kid: string;
    readonly publicJwk: RsaPublicJwk;
    readonly localState: "PENDING_REGISTRATION" | "READY" | "RETIRED" | "REVOKED";
    readonly createdAt: string;
}
export interface RsaSigner {
    readonly kid: string;
    readonly algorithm: "RS256";
    readonly publicJwk: RsaPublicJwk;
    /** RSASSA-PKCS1-v1_5 with SHA-256; message is the JWS signing input, not a prehash. */
    sign(message: Uint8Array, signal: AbortSignal): Promise<Uint8Array>;
}
/** Operator/runtime port. Do not expose these methods as model tools. */
export interface AgentKeyStore {
    /** Explicit initialization: never replaces an existing identity or key silently. */
    initialize(owner: AgentKeyOwner): Promise<PublicAgentKey>;
    stageRotation(owner: AgentKeyOwner): Promise<PublicAgentKey>;
    readPublic(owner: AgentKeyOwner, kid: string): Promise<PublicAgentKey>;
    /** Changes local selection only. AKB registration and approval remain authoritative. */
    selectRegisteredKey(owner: AgentKeyOwner, kid: string): Promise<void>;
    /** Pins a key version for the request; private bytes never cross this interface. */
    openActiveSigner(owner: AgentKeyOwner): Promise<RsaSigner>;
    markRetired(owner: AgentKeyOwner, kid: string): Promise<void>;
    markRevoked(owner: AgentKeyOwner, kid: string): Promise<void>;
}
export interface ExactHttpRequest {
    readonly method: "GET" | "POST";
    /** Absolute path and raw query only, e.g. /api/mcp; never a URL supplied by the model. */
    readonly target: string;
    /** A private immutable snapshot is made by the client before hashing and sending. */
    readonly body: Uint8Array;
}
export interface BoundRequestProofIssuer {
    /** The issuer is already bound to an approved Agent; there is no agentId argument. */
    issue(request: ExactHttpRequest, credentialSha256: string, signal: AbortSignal): Promise<{
        readonly headerName: "X-AKB-Workload-Assertion";
        readonly value: string;
        readonly kid: string;
        readonly jti: string;
        readonly expiresAt: number;
    }>;
}
export interface CredentialSource {
    /** One snapshot per HTTP request, used for both credential hash and X-API-Key. */
    readServiceKey(signal: AbortSignal): Promise<string>;
}
export interface IdentityFailure {
    readonly code: "IDENTITY_UNAVAILABLE" | "AGENT_UNREGISTERED" | "WORKSPACE_UNAPPROVED" | "APPROVAL_DENIED" | "CANCELLED" | "KEY_UNAVAILABLE" | "KEY_NOT_REGISTERED" | "PROTOCOL_UNSUPPORTED" | "AUTHENTICATION_FAILED" | "AUTHORIZATION_DENIED" | "RATE_LIMITED" | "UPSTREAM_UNAVAILABLE";
    readonly safeMessage: string;
    readonly retryable: boolean;
}
export interface AgentIdentityRuntime<NativeContext> {
    /** Implementation must synchronously call adapter.capture(native) before its first await. */
    invoke(native: NativeContext, request: KnowledgeRequest): Promise<{
        readonly data: JsonValue;
        readonly metadata: {
            readonly invocationId: string;
            readonly externalAgentId: string;
            readonly runtimeAgentKey: string;
        };
    }>;
}
export interface RuntimeDependencies<NativeContext> {
    readonly adapter: PlatformAdapter<NativeContext>;
    readonly bindings: readonly AgentBinding[];
    readonly keyStore: AgentKeyStore;
    readonly credentials: CredentialSource;
    readonly transport: "mcp" | "rest";
    readonly origin: string;
    readonly validateWorkspace: (directory: string) => Promise<boolean>;
    readonly allowInsecureLoopback?: boolean;
    readonly timeoutMs?: number;
}
/** Factory shape for dependency injection into a platform plugin. */
export type RuntimeFactory = <NativeContext>(dependencies: RuntimeDependencies<NativeContext>) => AgentIdentityRuntime<NativeContext>;
