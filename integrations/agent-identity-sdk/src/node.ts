import { realpath } from "node:fs/promises";
import { canonicalOrigin, identifier, IdentityError, ownerOf } from "./core.js";
import { NodeFileKeyStore, readProtectedFile } from "./keystore-node.js";
import { AkbKnowledgeClient } from "./client.js";
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
  /** Opt in to plain HTTP against private address space (RFC1918 / link-local). */
  allowPrivateHttp?: boolean;
  timeoutMs?: number;
}
export async function loadNodeConfig(filename: string, workspace: string): Promise<NodeIdentityConfig> {
  const root = await realpath(workspace);
  const config = JSON.parse(await readProtectedFile(filename, root)) as NodeIdentityConfig;
  if (config.configVersion !== 2 || config.proofProfile !== "AGENT_KEY_RS256_V2" || config.keyStore?.provider !== "node-file"
    || !identifier(config.platformId) || !identifier(config.installationId) || !identifier(config.workspaceId)
    || !Array.isArray(config.bindings) || config.bindings.length === 0 || config.bindings.length > 100
    || await realpath(config.workspaceRoot) !== root) throw new IdentityError("PROTOCOL_UNSUPPORTED", "Invalid V2 identity configuration");
  const origin = canonicalOrigin(config.origin, config.allowInsecureLoopback, config.allowPrivateHttp);
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
    allowPrivateHttp: config.allowPrivateHttp,
    async validateWorkspace(directory) { try { return await realpath(directory) === approved; } catch { return false; } },
  });
}

/** Simplified AKB connections: only host Agent mapping, IDs and private-key file paths are configured. */
export async function loadSimpleNodeRuntime<NativeContext>(filename: string, workspace: string, adapter: PlatformAdapter<NativeContext>) {
  const approved=await realpath(workspace);
  const config=JSON.parse(await readProtectedFile(filename,approved));
  if(config.configVersion!==3 || config.proofProfile!=="AGENT_KEY_RS256_V2" || await realpath(config.workspaceRoot)!==approved
    || config.keyStore?.provider!=="node-file" || !Array.isArray(config.agents) || !config.agents.length || config.agents.length>100) {
    throw new IdentityError("PROTOCOL_UNSUPPORTED","Invalid simplified identity configuration");
  }
  const credentials={async readServiceKey(signal:AbortSignal){signal.throwIfAborted();return (await readProtectedFile(config.serviceKeyFile,approved)).trim();}};
  const client=new AkbKnowledgeClient(credentials,config.origin,config.transport,config.timeoutMs,config.allowInsecureLoopback,config.allowPrivateHttp);
  const store=await NodeFileKeyStore.open(config.keyStore.rootDirectory,approved);
  const bindings:AgentBinding[]=[];const mapping=new Map<string,AgentBinding>();const ids=new Set<string>();
  for(const local of config.agents){
    if(!identifier(local.runtimeAgentKey)||mapping.has(local.runtimeAgentKey)||ids.has(local.agentId))throw new IdentityError("AGENT_UNREGISTERED","Each host Agent and AKB Agent ID must have exactly one binding");
    const signal=AbortSignal.timeout(60000),binding=await client.configuration(local.agentId,signal);
    const owner=ownerOf(binding);
    const imported=await store.importPrivateKey(owner,await readProtectedFile(local.privateKeyFile,approved));
    const signer=await store.openSigner(owner,imported.kid);
    await client.status(binding,{sessionId:"plugin-startup",invocationId:crypto.randomUUID()},signer,signal);
    await store.selectRegisteredKey(owner,signer.kid);
    bindings.push(binding);mapping.set(local.runtimeAgentKey,binding);ids.add(local.agentId);
  }
  const translated:PlatformAdapter<NativeContext>={capture(native){
    // Capture the true host identity and approval fiber synchronously before any await.
    const captured=adapter.capture(native),binding=mapping.get(captured.context.runtimeAgentKey);
    if(!binding)throw new IdentityError("AGENT_UNREGISTERED","Executing host Agent is not bound");
    return {...captured,context:{...captured.context,platformId:binding.platformId,runtimeAgentKey:binding.runtimeAgentKey}};
  }};
  return createRuntime({adapter:translated,bindings,keyStore:store,credentials,origin:config.origin,transport:config.transport,
    timeoutMs:config.timeoutMs,allowInsecureLoopback:config.allowInsecureLoopback,
    async validateWorkspace(directory){try{return await realpath(directory)===approved;}catch{return false;}}
  });
}
