#!/usr/bin/env node
import { writeFile } from "node:fs/promises";
import path from "node:path";
import { IdentityError, ownerIndex, ownerOf } from "./core.js";
import { NodeFileKeyStore, readProtectedFile } from "./keystore-node.js";
import { loadNodeConfig } from "./node.js";
import { proveRegistration } from "./registration.js";
import { AkbKnowledgeClient } from "./client.js";
import type { AgentBinding, AgentKeyOwner } from "./types.js";

const signal = new AbortController().signal;
const print = (data: unknown) => console.log(JSON.stringify(data, null, 2));
const json = async (filename: string) => JSON.parse(await readProtectedFile(filename));
async function main() {
  const [group, command, ...args] = process.argv.slice(2);
  if (group === "identity" && command === "create" && args.length === 4) {
    const [filename, platformId, installationId, workspaceId] = args;
    const owner = { platformId, installationId, workspaceId, externalAgentId: crypto.randomUUID() };
    ownerIndex(owner);
    if (!path.isAbsolute(filename)) throw new IdentityError("KEY_UNAVAILABLE", "Owner file must be absolute");
    await writeFile(filename, JSON.stringify(owner, null, 2) + "\n", { flag: "wx", mode: 0o600 });
    print(owner); return;
  }
  if (group === "doctor" && command && args.length === 0) {
    const seed = await json(command), config = await loadNodeConfig(command, seed.workspaceRoot);
    const store = await NodeFileKeyStore.open(config.keyStore.rootDirectory, config.workspaceRoot);
    for (const binding of config.bindings) {
      try { print({ runtimeAgentKey: binding.runtimeAgentKey, kid: (await store.openActiveSigner(ownerOf(binding))).kid, ready: true }); }
      catch { print({ runtimeAgentKey: binding.runtimeAgentKey, ready: false }); }
    }
    return;
  }
  if (group === "key" && command === "select" && args.length === 3) {
    const [configFile, runtimeAgentKey, kid] = args, seed = await json(configFile);
    const config = await loadNodeConfig(configFile, seed.workspaceRoot), binding = config.bindings.find(b => b.runtimeAgentKey === runtimeAgentKey);
    if (!binding) throw new IdentityError("AGENT_UNREGISTERED", "Unknown runtime Agent");
    const store = await NodeFileKeyStore.open(config.keyStore.rootDirectory, config.workspaceRoot);
    const signer = await store.openSigner(ownerOf(binding), kid);
    const client = new AkbKnowledgeClient({ async readServiceKey() { return (await readProtectedFile(config.serviceKeyFile, config.workspaceRoot)).trim(); } },
      config.origin, config.transport, config.timeoutMs, config.allowInsecureLoopback);
    const status = await client.status(binding, { sessionId: "admin-key-probe", invocationId: crypto.randomUUID() }, signer, signal);
    await store.selectRegisteredKey(ownerOf(binding), kid); print(status); return;
  }
  if (group === "key" && command === "prove-registration" && args.length === 4) {
    const [root, bindingFile, kid, challengeFile] = args;
    const binding = await json(bindingFile) as AgentBinding, store = await NodeFileKeyStore.open(root);
    const signer = await store.openSigner(ownerOf(binding), kid), challenge = await json(challengeFile);
    console.log(await proveRegistration(binding, challenge, signer, signal)); return;
  }
  if ((group === "agent" && command === "init" && args.length === 2)
    || (group === "key" && ["rotate", "export-public", "retire", "revoke"].includes(command) && args.length === (command === "rotate" ? 2 : 3))) {
    const [root, ownerFile, kid] = args, owner = await json(ownerFile) as AgentKeyOwner;
    ownerIndex(owner); const store = await NodeFileKeyStore.open(root);
    if (group === "agent") print(await store.initialize(owner));
    else if (command === "rotate") print(await store.stageRotation(owner));
    else if (command === "export-public") {
      const key = await store.readPublic(owner, kid);
      print({ format: "akb-agent-key-registration/v1", ...key.owner, publicJwk: key.publicJwk });
    } else {
      if (command === "retire") await store.markRetired(owner, kid); else await store.markRevoked(owner, kid);
      print({ kid, localOnly: true, message: "Also change the AKB key status through the administrator API" });
    }
    return;
  }
  throw new IdentityError("PROTOCOL_UNSUPPORTED", "Usage: identity create <owner-file> <platform> <installation> <workspace>; agent init <root> <owner-file>; key rotate <root> <owner-file>; key export-public|retire|revoke <root> <owner-file> <kid>; key prove-registration <root> <binding-file> <kid> <challenge-file>; key select <config-file> <runtime-agent> <kid>; doctor <config-file>");
}
main().catch(error => {
  console.error(error instanceof IdentityError ? `${error.code}: ${error.message}` : "Identity command failed; verify protected files and administrative inputs");
  process.exitCode = 1;
});
