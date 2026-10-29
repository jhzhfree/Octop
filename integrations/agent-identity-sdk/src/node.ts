import { realpath } from "node:fs/promises";
import { canonicalOrigin, identifier, IdentityError } from "./core.js";
import { NodeFileKeyStore, readProtectedFile } from "./keystore-node.js";
import { createRuntime } from "./runtime.js";
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
  keyStore: { provider: "node-file"; rootDirectory: string };
  bindings: AgentBinding[];
  allowInsecureLoopback?: boolean;
  timeoutMs?: number;
}
export async function loadNodeConfig(filename: string, workspace: string): Promise<NodeIdentityConfig> {
  const root = await realpath(workspace);
  const config = JSON.parse(await readProtectedFile(filename, root)) as NodeIdentityConfig;
  if (config.configVersion !== 2 || config.proofProfile !== "AGENT_KEY_RS256_V2" || config.keyStore?.provider !== "node-file"
    || !identifier(config.platformId) || !identifier(config.installationId) || !identifier(config.workspaceId)
    || !Array.isArray(config.bindings) || config.bindings.length === 0 || config.bindings.length > 100
    || await realpath(config.workspaceRoot) !== root) throw new IdentityError("PROTOCOL_UNSUPPORTED", "Invalid V2 identity configuration");
  const origin = canonicalOrigin(config.origin, config.allowInsecureLoopback);
  for (const b of config.bindings) if (b.platformId !== config.platformId || b.installationId !== config.installationId
    || b.workspaceId !== config.workspaceId || b.resource !== origin) throw new IdentityError("AGENT_UNREGISTERED", "Binding scope does not match installation configuration");
  return config;
}
export async function loadNodeRuntime<NativeContext>(filename: string, workspace: string, adapter: PlatformAdapter<NativeContext>) {
  const config = await loadNodeConfig(filename, workspace), approved = await realpath(config.workspaceRoot);
  const keyStore = await NodeFileKeyStore.open(config.keyStore.rootDirectory, approved);
  return createRuntime({ adapter, bindings: config.bindings, keyStore,
    credentials: { async readServiceKey(signal) { signal.throwIfAborted(); return (await readProtectedFile(config.serviceKeyFile, approved)).trim(); } },
    origin: config.origin, transport: config.transport, timeoutMs: config.timeoutMs, allowInsecureLoopback: config.allowInsecureLoopback,
    async validateWorkspace(directory) { try { return await realpath(directory) === approved; } catch { return false; } },
  });
}
