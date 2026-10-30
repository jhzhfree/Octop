#!/usr/bin/env node
/** Trusted host IPC, not a model tool or network listener. Secrets travel through stdin only. */
import { NodeFileKeyStore } from "./keystore-node.js";
import { IdentityError, ownerIndex, type Invocation } from "./core.js";
import { proveRegistration, type RegistrationChallenge } from "./registration.js";
import { AkbKnowledgeClient } from "./client.js";
import type { AgentBinding, AgentKeyOwner, KnowledgeRequest } from "./types.js";

interface HostRequest {
  op: "capabilities" | "state" | "initialize" | "rotate" | "registration" | "activate" | "probe" | "execute";
  root: string;
  owner: AgentKeyOwner;
  binding: AgentBinding;
  kid?: string;
  challenge?: RegistrationChallenge;
  apiKey?: string;
  origin: string;
  transport: "mcp" | "rest";
  timeoutMs?: number;
  allowInsecureLoopback?: boolean;
  invocation: Invocation;
  request: KnowledgeRequest;
}
async function run(input: HostRequest): Promise<unknown> {
  if (input.op === "capabilities") return { version: "0.3.1", supported: process.platform !== "win32" && Number(process.versions.node.split(".")[0]) >= 22 };
  if (Number(process.versions.node.split(".")[0]) < 22) throw new IdentityError("PROTOCOL_UNSUPPORTED", "Node 22 or later is required");
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
  const client = new AkbKnowledgeClient({ async readServiceKey() { return input.apiKey ?? ""; } }, input.origin, input.transport, input.timeoutMs, input.allowInsecureLoopback);
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
  process.stdout.write(JSON.stringify({ ok: false, code: error instanceof IdentityError ? error.code : "IDENTITY_UNAVAILABLE" }));
  process.exitCode = 1;
});
