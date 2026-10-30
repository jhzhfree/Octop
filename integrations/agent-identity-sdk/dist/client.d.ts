import { type Invocation } from "./core.js";
import type { AgentBinding, CredentialSource, JsonValue, KnowledgeRequest, RsaSigner } from "./types.js";
export declare function validateRequest(request: KnowledgeRequest): KnowledgeRequest;
export declare class AkbKnowledgeClient {
    private readonly credentials;
    private readonly transport;
    private readonly timeoutMs;
    readonly origin: string;
    constructor(credentials: CredentialSource, origin: string, transport: "mcp" | "rest", timeoutMs?: number, insecureLoopback?: boolean);
    private request;
    configuration(agentId: string, signal: AbortSignal): Promise<AgentBinding>;
    status(binding: AgentBinding, invocation: Invocation, signer: RsaSigner, signal: AbortSignal): Promise<any>;
    execute(request: KnowledgeRequest, binding: AgentBinding, invocation: Invocation, signer: RsaSigner, signal: AbortSignal): Promise<JsonValue>;
    private restResult;
}
