export declare const generateTrustUUID: () => string;
export declare function generateKeyPair(directory: string, agentId?: string): Promise<{
    agentId: string;
    kid: string;
    directory: string;
    publicJwk: import("@akb/agent-keygen").PublicAgentJwk;
}>;
export interface VerifyTerminalOptions {
    agentName: string;
    agentId: string;
    privateKeyFile: string;
    serviceKeyFile: string;
    workspaceRoot: string;
    origin: string;
    timeoutMs?: number;
    allowInsecureLoopback?: boolean;
    allowPrivateHttp?: boolean;
}
/** Discover public coordinates, then independently prove key possession to REST and MCP. */
export declare function verifyTerminal(options: VerifyTerminalOptions, signal?: AbortSignal): Promise<{
    verified: true;
    agentName: string;
    agentId: string;
    rest: any;
    mcp: any;
}>;
/** Only creates a new directory; caller must choose a trusted parent outside the workspace. */
export declare function createProtectedTerminalDirectory(directory: string, workspaceRoot: string): Promise<void>;
export declare function writeProtectedTerminalFile(filename: string, contents: string): Promise<void>;
export interface TerminalAuditEvent {
    agentName: string;
    phase: "started" | "completed" | "failed";
    operation: string;
    invocationId?: string;
    agentId?: string;
}
/** Pre-existing, protected audit file. Never accepts headers, arguments or key material. */
export declare function appendTerminalAudit(filename: string, workspaceRoot: string, event: TerminalAuditEvent): Promise<void>;
