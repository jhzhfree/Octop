import type { AgentBinding, PlatformAdapter } from "./types.js";
export interface NodeIdentityConfig {
    configVersion: 2;
    proofProfile: "AGENT_KEY_RS256_V2";
    platformId: string;
    installationId: string;
    workspaceId: string;
    workspaceRoot: string;
    origin: string;
    transport: "mcp" | "rest";
    serviceKeyFile: string;
    keyStore: {
        provider: "node-file";
        rootDirectory: string;
    };
    bindings: AgentBinding[];
    allowInsecureLoopback?: boolean;
    timeoutMs?: number;
}
export declare function loadNodeConfig(filename: string, workspace: string): Promise<NodeIdentityConfig>;
export declare function loadNodeRuntime<NativeContext>(filename: string, workspace: string, adapter: PlatformAdapter<NativeContext>): Promise<import("./types.js").AgentIdentityRuntime<NativeContext>>;
/** Simplified AKB connections: only host Agent mapping, IDs and private-key file paths are configured. */
export declare function loadSimpleNodeRuntime<NativeContext>(filename: string, workspace: string, adapter: PlatformAdapter<NativeContext>): Promise<import("./types.js").AgentIdentityRuntime<NativeContext>>;
