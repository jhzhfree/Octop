#!/usr/bin/env node
/** Trusted host IPC, not a model tool or network listener. Secrets travel through stdin only. */
import path from "node:path";
import { mkdir } from "node:fs/promises";
import { generateToDirectory } from "@akb/agent-keygen";
import { readProtectedFile, NodeFileKeyStore } from "./keystore-node.js";
import { IdentityError, ownerIndex, uuid, sha256, bytes, type Invocation } from "./core.js";
import { protectionSupported } from "./permissions.js";
import { proveRegistration, type RegistrationChallenge } from "./registration.js";
import { AkbKnowledgeClient } from "./client.js";
import type { AgentBinding, AgentKeyOwner, KnowledgeRequest } from "./types.js";

interface HostRequest {
  op: "generate" | "connect" | "capabilities" | "state" | "initialize" | "rotate" | "registration" | "activate" | "probe" | "execute";
  root: string;
  owner: AgentKeyOwner;
  binding: AgentBinding;
  externalAgentId?: string;
  privateKey?: string;
  keyReference?: string;
  keyScope?: string;
  kid?: string;
  challenge?: RegistrationChallenge;
  apiKey?: string;
  origin: string;
  transport: "mcp" | "rest";
  timeoutMs?: number;
  allowInsecureLoopback?: boolean;
  /** Opt in to plain HTTP against private address space (RFC1918 / link-local). */
  allowPrivateHttp?: boolean;
  invocation: Invocation;
  request: KnowledgeRequest;
}
async function run(input: HostRequest): Promise<unknown> {
  if (input.op === "capabilities") return { version: "0.4.0", supported: Number(process.versions.node.split(".")[0]) >= 22 && protectionSupported() };
  if (Number(process.versions.node.split(".")[0]) < 22) throw new IdentityError("PROTOCOL_UNSUPPORTED", "Node 22 or later is required");
  const generatedDirectory = async () => {
    if (typeof input.keyScope !== "string" || !input.keyScope.length || input.keyScope.length > 128) throw new IdentityError("KEY_UNAVAILABLE", "Key scope required");
    return path.join(input.root, "generated", await sha256(bytes(input.keyScope)));
  };
  if (input.op === "generate") {
    await NodeFileKeyStore.open(input.root);
    const parent = await generatedDirectory();
    await mkdir(parent, {recursive:true,mode:0o700});
    const reference = crypto.randomUUID();
    const generated = await generateToDirectory({directory:path.join(parent, reference), agentId:input.externalAgentId});
    return {externalAgentId:generated.agentId, kid:generated.kid, publicJwk:generated.publicJwk, keyReference:reference};
  }
  if (input.op === "connect") {
    const signal = AbortSignal.timeout(Math.min(180000,(input.timeoutMs ?? 15000)*3+10000));
    const client = new AkbKnowledgeClient({async readServiceKey(){return input.apiKey ?? "";}},input.origin,input.transport,input.timeoutMs,input.allowInsecureLoopback,input.allowPrivateHttp);
    const binding=await client.configuration(input.externalAgentId ?? "",signal);
    const owner={platformId:binding.platformId,installationId:binding.installationId,workspaceId:binding.workspaceId,externalAgentId:binding.externalAgentId};
    const store=await NodeFileKeyStore.open(input.root);
    const state=await store.describe(owner);
    let pem = input.privateKey;
    if (input.keyReference) {
      if (pem || !uuid(input.keyReference)) throw new IdentityError("KEY_UNAVAILABLE", "Invalid generated key reference");
      const directory = path.join(await generatedDirectory(), input.keyReference);
      const metadata = JSON.parse(await readProtectedFile(path.join(directory,"identity.json")));
      if (metadata.agentId !== binding.externalAgentId) throw new IdentityError("KEY_UNAVAILABLE", "Generated key belongs to a different Agent");
      pem = await readProtectedFile(path.join(directory,"private.pem"));
    }
    const imported=pem ? await store.importPrivateKey(owner,pem) : undefined;
    const signer=await store.openSigner(owner,imported?.kid ?? state.activeKid ?? "");
    const status=await client.status(binding,input.invocation,signer,signal);
    await store.selectRegisteredKey(owner,signer.kid);
    return {binding,status,...await store.describe(owner)};
  }
  ownerIndex(input.owner);
  const store = await NodeFileKeyStore.open(input.root);
  if (input.op === "state") return store.describe(input.owner);
  if (input.op === "initialize" || input.op === "rotate") {
    if (input.op === "initialize") await store.initialize(input.owner); else await store.stageRotation(input.owner);
    return store.describe(input.owner);
  }
  const signal = AbortSignal.timeout(Math.min(180000, (input.timeoutMs ?? 15000) * 3 + 10000));
  if (input.op === "registration") {
    if (!input.kid || !input.challenge) throw new IdentityError("KEY_NOT_REGISTERED", "Registration challenge is required");
    return { registrationProof: await proveRegistration(input.binding, input.challenge, await store.openSigner(input.owner, input.kid), signal) };
  }
  const client = new AkbKnowledgeClient({ async readServiceKey() { return input.apiKey ?? ""; } }, input.origin, input.transport, input.timeoutMs, input.allowInsecureLoopback, input.allowPrivateHttp);
  if (input.op === "activate" || input.op === "probe") {
    const signer = input.op === "activate" && input.kid ? await store.openSigner(input.owner, input.kid) : await store.openActiveSigner(input.owner);
    const status = await client.status(input.binding, input.invocation, signer, signal);
    if (input.op === "activate") await store.selectRegisteredKey(input.owner, signer.kid);
    return { status, ...await store.describe(input.owner) };
  }
  if (input.op !== "execute") throw new IdentityError("PROTOCOL_UNSUPPORTED", "Unknown host operation");
  return client.execute(input.request, input.binding, input.invocation, await store.openActiveSigner(input.owner), signal);
}
async function main() {
  const chunks: Buffer[] = []; let size = 0;
  for await (const chunk of process.stdin) {
    const bytes = Buffer.from(chunk); size += bytes.length;
    if (size > 65536) throw new IdentityError("PROTOCOL_UNSUPPORTED", "Host request exceeds size limit");
    chunks.push(bytes);
  }
  const data = await run(JSON.parse(Buffer.concat(chunks).toString("utf8")));
  process.stdout.write(JSON.stringify({ ok: true, data }));
}
main().catch(error => {
  // The host needs the reason, not just the code: several failures share a code
  // (HTTPS origin, Node version, unknown operation) and only the message
  // distinguishes them.
  const identity = error instanceof IdentityError;
  process.stdout.write(JSON.stringify({
    ok: false,
    code: identity ? error.code : "IDENTITY_UNAVAILABLE",
    message: identity ? error.safeMessage : String((error as Error)?.message ?? error),
  }));
  process.exitCode = 1;
});
