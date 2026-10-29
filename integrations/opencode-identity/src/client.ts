import { createHash, createPrivateKey, createPublicKey, randomUUID, sign, type KeyObject } from "node:crypto";
import { readFile, realpath, stat } from "node:fs/promises";
import path from "node:path";

export interface IdentityConfig {
  origin: string;
  transport: "mcp" | "rest";
  serviceKeyFile: string;
  privateKeyFile: string;
  issuer: string;
  audience: string;
  applicationPrincipalId: string;
  installationId: string;
  workspaceId: string;
  workspaceRoot: string;
  agents: Record<string, string>;
  allowInsecureLoopback?: boolean;
  timeoutMs?: number;
}
export interface Invocation {
  runtimeAgent: string;
  externalAgentId: string;
  sessionId: string;
  messageId: string;
  invocationId: string;
}
export type Operation = "search" | "query" | "bases";
const sha256 = (text: string) => createHash("sha256").update(text).digest("hex");
const encoded = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
const identifier = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9_.:-]{1,128}$/.test(value);

export function publicJwk(key: KeyObject) {
  const jwk = createPublicKey(key).export({ format: "jwk" });
  const kid = createHash("sha256").update(JSON.stringify({ e: jwk.e, kty: jwk.kty, n: jwk.n })).digest("base64url");
  return { kty: "RSA", n: jwk.n, e: jwk.e, kid, alg: "RS256", use: "sig" };
}

async function protectedFile(filename: string, workspace: string) {
  if (!path.isAbsolute(filename)) throw new Error("Identity configuration paths must be absolute");
  const actual = await realpath(filename);
  const relative = path.relative(workspace, actual);
  if (!relative || (!relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative))) {
    throw new Error("Identity files must be outside the agent workspace");
  }
  const info = await stat(actual);
  if (!info.isFile() || (process.platform !== "win32" && ((info.mode & 0o077) !== 0 || info.uid !== process.getuid?.()))) {
    throw new Error("Identity files must be owner-only files (0600)");
  }
  return readFile(actual, "utf8");
}
/** Preserve the V1 loader's platform behavior; V2 applies its Provider checks afterward. */
export async function readIdentityConfiguration(filename: string, workspace: string) {
  return JSON.parse(await protectedFile(filename, await realpath(workspace)));
}

export class WorkloadClient {
  readonly config: IdentityConfig;
  private readonly key: KeyObject;
  private readonly serviceKey: string;
  private readonly jwk: ReturnType<typeof publicJwk>;

  private constructor(config: IdentityConfig, key: KeyObject, serviceKey: string) {
    this.config = config; this.key = key; this.serviceKey = serviceKey; this.jwk = publicJwk(key);
  }
  static async load(filename: string, workspace: string) {
    const root = await realpath(workspace);
    let config: IdentityConfig;
    try { config = JSON.parse(await protectedFile(filename, root)); }
    catch { throw new Error("Cannot load protected AKB identity configuration"); }
    if (!config || !["mcp", "rest"].includes(config.transport) || !identifier(config.installationId)
      || !identifier(config.workspaceId) || !identifier(config.audience)
      || !/^\d+$/.test(config.applicationPrincipalId) || !config.agents
      || typeof config.issuer !== "string" || config.issuer.length > 255
      || (config.timeoutMs !== undefined && (!Number.isInteger(config.timeoutMs) || config.timeoutMs < 100 || config.timeoutMs > 60000))) {
      throw new Error("Invalid AKB identity configuration");
    }
    if (await realpath(config.workspaceRoot) !== root) throw new Error("Workspace is not approved by this identity configuration");
    for (const [agent, external] of Object.entries(config.agents)) {
      if (!identifier(agent) || !identifier(external)) throw new Error("Invalid approved Agent mapping");
    }
    const origin = new URL(config.origin);
    const loopback = config.allowInsecureLoopback === true && origin.protocol === "http:"
      && ["localhost", "127.0.0.1", "[::1]"].includes(origin.hostname);
    if ((origin.protocol !== "https:" && !loopback) || origin.username || origin.password || origin.search || origin.hash || origin.pathname !== "/") {
      throw new Error("AKB origin must be an HTTPS origin (explicit loopback development override only)");
    }
    const key = createPrivateKey(await protectedFile(config.privateKeyFile, root));
    if (key.asymmetricKeyType !== "rsa" || (key.asymmetricKeyDetails?.modulusLength ?? 0) < 2048) throw new Error("Signing requires an RSA key of at least 2048 bits");
    const serviceKey = (await protectedFile(config.serviceKeyFile, root)).trim();
    if (!serviceKey || /[\r\n]/.test(serviceKey)) throw new Error("Invalid service credential file");
    return new WorkloadClient({ ...config, origin: origin.origin, workspaceRoot: root }, key, serviceKey);
  }
  async invocation(context: { agent: string; sessionID: string; messageID: string; directory: string }): Promise<Invocation> {
    if (await realpath(context.directory) !== this.config.workspaceRoot) throw new Error("Unapproved execution workspace");
    const agent = context.agent;
    if (!Object.hasOwn(this.config.agents, agent) || !identifier(context.sessionID) || !identifier(context.messageID)) {
      throw new Error("Missing or unregistered runtime Agent identity");
    }
    return Object.freeze({ runtimeAgent: agent, externalAgentId: this.config.agents[agent], sessionId: context.sessionID,
      messageId: context.messageID, invocationId: randomUUID() });
  }
  private proof(invocation: Invocation, method: string, target: string, body: string) {
    const now = Math.floor(Date.now() / 1000);
    const header = encoded({ alg: "RS256", typ: "JWT", kid: this.jwk.kid });
    const payload = encoded({ iss: this.config.issuer, aud: this.config.audience, sub: `agent:${invocation.externalAgentId}`,
      azp: this.config.applicationPrincipalId, installation_id: this.config.installationId, workspace_id: this.config.workspaceId,
      runtime_agent: invocation.runtimeAgent, session_id: invocation.sessionId, message_id: invocation.messageId,
      invocation_id: invocation.invocationId, iat: now, exp: now + 60, jti: randomUUID(),
      request_binding: { method, target, body_sha256: sha256(body) } });
    const input = `${header}.${payload}`;
    return `${input}.${sign("RSA-SHA256", Buffer.from(input), this.key).toString("base64url")}`;
  }
  private async request(invocation: Invocation, method: string, target: string, payload: unknown, signal: AbortSignal) {
    signal.throwIfAborted();
    const body = payload === undefined ? "" : JSON.stringify(payload);
    const response = await fetch(this.config.origin + target, { method, redirect: "error",
      headers: { "X-API-Key": this.serviceKey, "X-AKB-Workload-Assertion": this.proof(invocation, method, target, body),
        "Content-Type": "application/json", Accept: "application/json" },
      body: method === "GET" ? undefined : body,
      signal: AbortSignal.any([signal, AbortSignal.timeout(this.config.timeoutMs ?? 15000)]) });
    // Bound even chunked upstream responses; never include raw upstream errors or credentials.
    const reader = response.body?.getReader();
    let size = 0; const chunks: Uint8Array[] = [];
    if (reader) {
      try {
        while (true) {
          const chunk = await reader.read(); if (chunk.done) break;
          size += chunk.value.byteLength;
          if (size > 2_000_000) throw new Error("AKB response exceeds size limit");
          chunks.push(chunk.value);
        }
      } finally { await reader.cancel(); }
    }
    if (!response.ok) throw new Error(`AKB request rejected (HTTP ${response.status})`);
    let result: any;
    try { result = JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch { throw new Error("Invalid AKB JSON response"); }
    return result;
  }
  async execute(operation: Operation, args: { query?: string; topK?: number; knowledgeBaseCode?: string; knowledgeBaseId?: number },
    invocation: Invocation, signal: AbortSignal) {
    if (this.config.transport === "mcp") {
      const rpc = async (method: string, params: unknown) => {
        const id = randomUUID();
        const result = await this.request(invocation, "POST", "/api/mcp", { jsonrpc: "2.0", id, method, params }, signal);
        if (result.id !== id || result.jsonrpc !== "2.0" || result.error || !result.result || result.result.isError) throw new Error("AKB rejected the MCP operation");
        return result.result;
      };
      const init = await rpc("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "akb-opencode-identity", version: "0.1.0" } });
      if (init.protocolVersion !== "2025-06-18") throw new Error("Unsupported AKB MCP protocol");
      const { query, topK, knowledgeBaseId, knowledgeBaseCode } = args;
      const filters = { topK, knowledgeBaseId, knowledgeBaseCode };
      const name = operation === "bases" ? "list_knowledge_bases" : operation === "search" ? "knowledge_search" : "knowledge_query";
      return rpc("tools/call", { name, arguments: operation === "bases" ? {} : { ...filters, [operation === "search" ? "query" : "question"]: query } });
    }
    if (operation === "bases") return this.request(invocation, "GET", "/api/v1/external/bases", undefined, signal);
    let knowledgeBaseId = args.knowledgeBaseId;
    if (args.knowledgeBaseCode) {
      const bases = await this.request(invocation, "GET", "/api/v1/external/bases", undefined, signal);
      if (bases.success !== true || !Array.isArray(bases.data)) throw new Error("AKB base discovery failed");
      const match = bases.data.find((base: any) => base.baseCode === args.knowledgeBaseCode);
      if (!match || !Number.isSafeInteger(match.id) || (knowledgeBaseId !== undefined && knowledgeBaseId !== match.id)) {
        throw new Error("Unknown, unauthorized or mismatched AKB base reference");
      }
      knowledgeBaseId = match.id;
    }
    const result = await this.request(invocation, "POST", `/api/v1/external/${operation}`,
      { knowledgeBaseId, question: args.query, topK: args.topK }, signal);
    if (result.success === false) throw new Error("AKB rejected the REST operation");
    return result;
  }
}
