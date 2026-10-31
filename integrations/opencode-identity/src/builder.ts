/** Operator-only OpenCode plugin builder; never register these functions as model tools. */
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { realpath, rm } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";
import { canonicalOrigin, identifier, uuid } from "@akb/agent-identity-sdk/core";
import { readProtectedFile } from "@akb/agent-identity-sdk/keystore-node";
import { generateKeyPair, createProtectedTerminalDirectory, writeProtectedTerminalFile, verifyTerminal } from "@akb/agent-identity-sdk/terminal";

const exec = promisify(execFile);
export const SUPPORTED_OPENCODE_VERSION = "1.14.30";
export async function inspectOpenCode(binary = process.platform === "win32" ? "opencode.exe" : "opencode", workspaceRoot?: string) {
  // execFile, never a shell: paths and Agent names cannot become shell commands.
  const options = { cwd: workspaceRoot ? await realpath(workspaceRoot) : undefined, encoding: "utf8" as const, windowsHide: true, timeout: 20000, maxBuffer: 1024 * 1024 };
  const versionOutput = (await exec(binary, ["--version"], options)).stdout.trim();
  const version = /(?:^|\s)(\d+\.\d+\.\d+)(?:\s|$)/.exec(versionOutput)?.[1];
  if (!version) throw new Error("Cannot parse OpenCode version");
  const output = (await exec(binary, ["agent", "list"], options)).stdout;
  const agents = [...output.replace(/\x1b\[[0-9;]*m/g, "").matchAll(/^([^\s]+)\s+\((?:primary|subagent|all)\)\s*$/gm)].map(m => m[1]);
  if (!agents.length || agents.some(name => !identifier(name))) throw new Error("Cannot read OpenCode Agent list; unsupported CLI output");
  return { version, supported: version === SUPPORTED_OPENCODE_VERSION, agents: [...new Set(agents)] };
}
export interface BuilderInput {
  workspaceRoot: string;
  origin: string;
  allowPrivateHttp?: boolean;
  serviceKeyFile: string;
  agents: { name: string; agentId?: string; keyDirectory?: string }[];
  /** Optional absolute audit filename in the new output directory. */
  auditFile?: string;
  /** Fixed protocol field: cannot be changed without breaking the server verifier. */
  assertionHeader?: "X-AKB-Workload-Assertion";
  authenticationPath?: "/api/v1/external/identity/status";
  mcpPath?: "/api/mcp";
}
export async function buildPlugin(input: BuilderInput, output: string, binary?: string) {
  const workspace = await realpath(input.workspaceRoot);
  const host = await inspectOpenCode(binary, workspace);
  if (!host.supported) throw new Error(`OpenCode ${host.version} is not validated; required ${SUPPORTED_OPENCODE_VERSION}`);
  const origin = canonicalOrigin(input.origin, false, input.allowPrivateHttp);
  if (input.assertionHeader !== undefined && input.assertionHeader !== "X-AKB-Workload-Assertion"
    || input.authenticationPath !== undefined && input.authenticationPath !== "/api/v1/external/identity/status"
    || input.mcpPath !== undefined && input.mcpPath !== "/api/mcp") throw new Error("Authentication fields must match the AKB V2 protocol");
  if (!Array.isArray(input.agents) || !input.agents.length || input.agents.length > 100) throw new Error("Select 1 to 100 Agents");
  const names = new Set<string>(), ids = new Set<string>();
  for (const agent of input.agents) {
    if (!identifier(agent.name) || !host.agents.includes(agent.name) || names.has(agent.name)
      || agent.agentId !== undefined && (!uuid(agent.agentId) || ids.has(agent.agentId))
      || agent.keyDirectory !== undefined && (!path.isAbsolute(agent.keyDirectory) || !agent.agentId)) throw new Error("Invalid, duplicate or unknown Agent binding");
    names.add(agent.name); if (agent.agentId) ids.add(agent.agentId);
  }
  // Verify the credential location/protection before generating any identity material.
  const serviceKey = (await readProtectedFile(input.serviceKeyFile, workspace)).trim();
  if (!/^[A-Za-z0-9_-]+$/.test(serviceKey)) throw new Error("Invalid service credential");
  output = path.resolve(output);
  const auditFile = input.auditFile ?? path.join(output, "audit.jsonl");
  if (!path.isAbsolute(auditFile) || path.dirname(auditFile) !== output) throw new Error("Audit file must be directly inside the new output directory");
  const reserved = new Set(["identity.json", "plugin.mjs", "opencode.fragment.json", "enrollment.json", "keys"]);
  if (input.agents.some(a => reserved.has(a.name))) throw new Error("Agent name conflicts with a bundle artifact");
  if (reserved.has(path.basename(auditFile)) || input.agents.some((_, i) => `agent-${i + 1}` === path.basename(auditFile))) throw new Error("Audit file conflicts with a bundle artifact");
  await createProtectedTerminalDirectory(output, workspace);
  try {
    const agents: { runtimeAgentKey: string; agentId: string; privateKeyFile: string }[] = [];
    const enrollment: { name: string; agentId: string; publicKeyFile: string }[] = [];
    for (const agent of input.agents) {
      const directory = agent.keyDirectory ?? path.join(output, `agent-${agents.length + 1}`);
      let agentId = agent.agentId;
      if (agent.keyDirectory) {
        const identity = JSON.parse(await readProtectedFile(path.join(directory, "identity.json"), workspace));
        if (identity.agentId !== agentId) throw new Error("Existing key identity does not match the trust UUID");
        await readProtectedFile(path.join(directory, "private.pem"), workspace);
      } else {
        const generated = await generateKeyPair(directory, agentId); agentId = generated.agentId;
      }
      if (!agentId) throw new Error("Missing Agent UUID");
      agents.push({ runtimeAgentKey: agent.name, agentId, privateKeyFile: path.join(directory, "private.pem") });
      enrollment.push({ name: agent.name, agentId, publicKeyFile: path.join(directory, "public.jwk") });
    }
    const configFile = path.join(output, "identity.json");
    await writeProtectedTerminalFile(configFile, JSON.stringify({ configVersion: 3, proofProfile: "AGENT_KEY_RS256_V2",
      workspaceRoot: workspace, origin, allowPrivateHttp: input.allowPrivateHttp, transport: "mcp", serviceKeyFile: input.serviceKeyFile,
      keyStore: { provider: "node-file", rootDirectory: path.join(output, "keys") }, agents, auditFile }, null, 2));
    await writeProtectedTerminalFile(auditFile, "");
    // Resolve the installed entry, so an installed TGZ works without a sibling checkout.
    const entry = pathToFileURL(fileURLToPath(new URL("./index.js", import.meta.url))).href;
    await writeProtectedTerminalFile(path.join(output, "plugin.mjs"),
      `import plugin from ${JSON.stringify(entry)};\nexport default { id: plugin.id, server: input => plugin.server(input, { configFile: ${JSON.stringify(configFile)} }) };\n`);
    await writeProtectedTerminalFile(path.join(output, "opencode.fragment.json"), JSON.stringify({
      plugin: [pathToFileURL(path.join(output, "plugin.mjs")).href],
      permission: { akb_bases: "ask", akb_search: "ask", akb_query: "ask" } }, null, 2));
    await writeProtectedTerminalFile(path.join(output, "enrollment.json"), JSON.stringify(enrollment, null, 2));
    return { output, configFile, enrollment, opencodeVersion: host.version, auditFile };
  } catch (e) { await rm(output, { recursive: true, force: true }); throw e; }
}
export async function verifyPlugin(configFile: string, workspaceRoot: string, name: string) {
  const config = JSON.parse(await readProtectedFile(configFile, workspaceRoot));
  if (config.configVersion !== 3 || config.proofProfile !== "AGENT_KEY_RS256_V2" || await realpath(config.workspaceRoot) !== await realpath(workspaceRoot)) throw new Error("Expected a matching simplified plugin configuration");
  const agents = config.agents?.filter((a: { runtimeAgentKey: string }) => a.runtimeAgentKey === name);
  if (agents?.length !== 1) throw new Error("Agent is not uniquely configured");
  return verifyTerminal({ agentName: name, agentId: agents[0].agentId, privateKeyFile: agents[0].privateKeyFile,
    origin: config.origin, serviceKeyFile: config.serviceKeyFile, workspaceRoot, timeoutMs: config.timeoutMs, allowPrivateHttp: config.allowPrivateHttp });
}
