import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, chmod, readdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { createServer } from "node:http";
import { once } from "node:events";
import { createPublicKey, verify, createHash } from "node:crypto";
import { createRuntime } from "../dist/runtime.js";
import { NodeFileKeyStore } from "../dist/keystore-node.js";
import { AkbKnowledgeClient } from "../dist/client.js";
import { ownerOf, ownerIndex, bytes, sha256, publicKid } from "../dist/core.js";
import { proveRegistration } from "../dist/registration.js";

const posixTest = process.platform === "win32" ? test.skip : test;
const ids = ["e21cce9c-1b16-4ab8-a76a-bb761df919b8", "01965897-0976-471c-8d64-27eb682a227b"];
const owner = id => ({ platformId: "test-platform", installationId: "install", workspaceId: "workspace", externalAgentId: id });
const signal = () => new AbortController().signal;

async function fixture(transport = "mcp") {
  const root = await mkdtemp(path.join(tmpdir(), "akb-sdk-")), workspace = path.join(root, "workspace"); await mkdir(workspace);
  const store = await NodeFileKeyStore.open(path.join(root, "keys"), workspace);
  const keys = await Promise.all(ids.map(id => store.initialize(owner(id))));
  await Promise.all(keys.map(k => store.selectRegisteredKey(k.owner, k.kid)));
  const calls = [], errors = [];
  let bindings;
  const server = createServer(async (req, res) => {
    try {
      const chunks = []; for await (const chunk of req) chunks.push(chunk);
      const raw = Buffer.concat(chunks), [h,p,s] = req.headers["x-akb-workload-assertion"].split(".");
      const header = JSON.parse(Buffer.from(h, "base64url")), claims = JSON.parse(Buffer.from(p, "base64url"));
      const index = ids.findIndex(id => claims.sub === `agent:${id}`), binding = bindings[index];
      if (!binding || header.kid !== keys[index].kid) { res.writeHead(401).end(); return; }
      assert.ok(verify("RSA-SHA256", Buffer.from(`${h}.${p}`), createPublicKey({ key: keys[index].publicJwk, format: "jwk" }), Buffer.from(s, "base64url")));
      assert.equal(claims.ver, 2); assert.equal(header.typ, "akb-agent-proof+jwt");
      assert.equal(claims.credential_sha256, createHash("sha256").update(req.headers["x-api-key"]).digest("hex"));
      assert.equal(claims.resource, binding.resource);
      assert.equal(claims.request_binding.method, req.method); assert.equal(claims.request_binding.target, req.url);
      assert.equal(claims.request_binding.body_sha256, createHash("sha256").update(raw).digest("hex"));
      const body = raw.length ? JSON.parse(raw) : {};
      calls.push({ header, claims, body, serviceKey: req.headers["x-api-key"] });
      res.setHeader("Content-Type", "application/json");
      if (transport === "mcp") res.end(JSON.stringify({ jsonrpc: "2.0", id: body.id, result: body.method === "initialize"
        ? { protocolVersion: "2025-06-18" } : { content: [{ type: "text", text: claims.sub }] } }));
      else res.end(JSON.stringify({ success: true, data: req.method === "GET" ? [{ id: index + 2, baseCode: `PRIVATE-${index}` }] : { candidates: [{ content: claims.sub }] } }));
    } catch (e) { errors.push(e); res.writeHead(500).end(); }
  });
  server.listen(0, "127.0.0.1"); await once(server, "listening");
  const origin = `http://127.0.0.1:${server.address().port}`;
  bindings = ids.map((id, i) => ({ ...owner(id), policyId: "17", bindingId: String(21+i), runtimeAgentKey: i ? "review" : "build",
    applicationPrincipalId: "123", issuer: "https://issuer.example/test", audience: "akb", resource: origin }));
  const captured = [];
  const adapter = { capture(native) {
    captured.push(native.agent);
    return { context: Object.freeze({ platformId: "test-platform", runtimeAgentKey: native.agent, sessionId: native.session,
      workspaceRoot: native.directory, abortSignal: native.signal }), authorize: native.approve };
  } };
  const dependencies = { adapter, keyStore: store, bindings, credentials: { async readServiceKey() { return "shared-test-key"; } },
    transport, origin, allowInsecureLoopback: true, async validateWorkspace(directory) { return directory === workspace; } };
  const context = (agent, approve = async () => {}) => ({ agent, approve, session: `session-${agent}`, directory: workspace, signal: signal() });
  return { root, workspace, store, keys, bindings, calls, errors, captured, dependencies, context,
    async close() { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); await rm(root, { recursive: true, force: true }); assert.deepEqual(errors, []); } };
}

posixTest("independent platform adapter: concurrent Agents share one credential and keep separate keys and identity", async () => {
  const f = await fixture();
  try {
    const runtime = createRuntime(f.dependencies);
    const first = runtime.invoke(f.context("build"), { operation: "search", query: "a", agentId: ids[1] });
    assert.deepEqual(f.captured, ["build"], "capture executes before invoke yields");
    const second = runtime.invoke(f.context("review"), { operation: "search", query: "b" });
    const [a,b] = await Promise.all([first, second]);
    assert.equal(a.metadata.externalAgentId, ids[0]); assert.equal(b.metadata.externalAgentId, ids[1]);
    assert.equal(f.calls.length, 4); assert.equal(new Set(f.calls.map(x => x.claims.jti)).size, 4);
    assert.equal(new Set(f.calls.map(x => x.header.kid)).size, 2); assert.equal(new Set(f.calls.map(x => x.serviceKey)).size, 1);
    for (const call of f.calls.filter(x => x.body.method === "tools/call")) {
      assert.equal(call.body.params.arguments.agentId, undefined);
      assert.equal(call.claims.sub, `agent:${call.body.params.arguments.query === "a" ? ids[0] : ids[1]}`);
    }
  } finally { await f.close(); }
});
posixTest("denial, cancellation, unknown identity and workspace mismatch send no requests", async () => {
  const f = await fixture();
  try {
    const runtime = createRuntime(f.dependencies);
    let approvals = 0;
    await assert.rejects(runtime.invoke(f.context("build", async () => { approvals++; throw new Error("DENIED"); }), { operation: "bases" }), /DENIED/);
    const aborted = new AbortController(); aborted.abort();
    await assert.rejects(runtime.invoke({ ...f.context("build"), signal: aborted.signal }, { operation: "bases" }));
    await assert.rejects(runtime.invoke(f.context("forged"), { operation: "bases" }), /unregistered/);
    await assert.rejects(runtime.invoke({ ...f.context("build"), directory: f.root }, { operation: "bases" }), /workspace/);
    assert.equal(approvals, 1); assert.equal(f.calls.length, 0);
  } finally { await f.close(); }
});
posixTest("REST signs discovery and data; mismatched base references are rejected", async () => {
  const f = await fixture("rest");
  try {
    const runtime = createRuntime(f.dependencies);
    const result = await runtime.invoke(f.context("build"), { operation: "search", query: "规则🚀", knowledgeBaseCode: "PRIVATE-0" });
    assert.match(JSON.stringify(result.data), new RegExp(ids[0]));
    assert.equal(f.calls[1].body.knowledgeBaseId, 2);
    await assert.rejects(runtime.invoke(f.context("build"), { operation: "search", query: "x", knowledgeBaseCode: "PRIVATE-0", knowledgeBaseId: 99 }), /mismatched/);
  } finally { await f.close(); }
});
posixTest("a valid A signature cannot be used for B with the same service credential", async () => {
  const f = await fixture();
  try {
    const client = new AkbKnowledgeClient(f.dependencies.credentials, f.dependencies.origin, "mcp", 15000, true);
    const signerA = await f.store.openActiveSigner(owner(ids[0]));
    await assert.rejects(client.execute({ operation: "bases" }, f.bindings[1], { sessionId: "attack", invocationId: crypto.randomUUID() }, signerA, signal()), /401/);
    assert.equal(f.calls.length, 0);
  } finally { await f.close(); }
});
posixTest("initialization is exclusive, restart-stable, and rotation preserves the old key until selection", async () => {
  const root = await mkdtemp(path.join(tmpdir(), "akb-key-lifecycle-"));
  try {
    const store = await NodeFileKeyStore.open(path.join(root, "keys"));
    const [a,b] = await Promise.all([store.initialize(owner(ids[0])), store.initialize(owner(ids[0]))]);
    assert.equal(a.kid, b.kid); assert.equal(a.localState, "PENDING_REGISTRATION");
    await assert.rejects(store.openActiveSigner(owner(ids[0])), /registered/);
    await store.selectRegisteredKey(owner(ids[0]), a.kid);
    const next = await store.stageRotation(owner(ids[0])); assert.notEqual(next.kid, a.kid);
    assert.equal((await store.openActiveSigner(owner(ids[0]))).kid, a.kid);
    const reopened = await NodeFileKeyStore.open(path.join(root, "keys"));
    assert.equal((await reopened.initialize(owner(ids[0]))).kid, a.kid);
    await reopened.selectRegisteredKey(owner(ids[0]), next.kid);
    await reopened.markRevoked(owner(ids[0]), a.kid);
    await assert.rejects(reopened.selectRegisteredKey(owner(ids[0]), a.kid), /revoked/);
    assert.equal((await reopened.openActiveSigner(owner(ids[0]))).kid, next.kid);
    const directory = path.join(root, "keys", await sha256(bytes(ownerIndex(owner(ids[0])))));
    await rm(path.join(directory, next.kid, "private.pem"));
    await assert.rejects(reopened.openActiveSigner(owner(ids[0])));
    assert.equal((await readdir(directory)).filter(n => /^[A-Za-z0-9_-]{43}$/.test(n)).length, 2, "missing key is not regenerated");
  } finally { await rm(root, { recursive: true, force: true }); }
});
posixTest("unsafe permissions and workspace key stores fail closed", async () => {
  const f = await fixture();
  try {
    await assert.rejects(NodeFileKeyStore.open(path.join(f.workspace, "keys"), f.workspace), /outside/);
    const directory = path.join(f.root, "keys", await sha256(bytes(ownerIndex(owner(ids[0])))));
    const filename = path.join(directory, f.keys[0].kid, "private.pem");
    await chmod(filename, 0o644);
    await assert.rejects(f.store.openActiveSigner(owner(ids[0])), /owner-only/);
  } finally { await f.close(); }
});
posixTest("registration proof binds purpose, challenge, Agent and key; mismatches are not signed", async () => {
  const f = await fixture();
  try {
    const signer = await f.store.openActiveSigner(owner(ids[0]));
    const challenge = { challengeId: crypto.randomUUID(), nonce: "a".repeat(43), policyId: "17", bindingId: "21", externalAgentId: ids[0],
      jkt: signer.kid, purpose: "akb.agent-key.register", audience: "akb-agent-key-registration", expiresAt: Math.floor(Date.now()/1000)+300 };
    const token = await proveRegistration(f.bindings[0], challenge, signer, signal());
    const [h,p,s] = token.split("."); const claims = JSON.parse(Buffer.from(p, "base64url"));
    assert.equal(JSON.parse(Buffer.from(h,"base64url")).typ, "akb-key-registration+jwt");
    assert.equal(claims.challenge_id, challenge.challengeId);
    assert.ok(verify("RSA-SHA256", Buffer.from(`${h}.${p}`), createPublicKey({ key: signer.publicJwk, format: "jwk" }), Buffer.from(s,"base64url")));
    await assert.rejects(proveRegistration(f.bindings[1], challenge, signer, signal()), /owner/);
    await assert.rejects(proveRegistration(f.bindings[0], { ...challenge, expiresAt: 1 }, signer, signal()), /owner/);
  } finally { await f.close(); }
});
test("public RFC7638 fixture agrees with the portable core", async () => {
  const fixture = JSON.parse(await readFile(new URL("../../agent-identity-spec/examples/protocol-v2-vectors.json", import.meta.url)));
  for (const agent of fixture.agents) assert.equal(await publicKid(agent.publicJwk), agent.publicJwk.kid);
});
