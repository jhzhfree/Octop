import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, rm, chmod } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { createServer } from "node:http";
import { createHash, createPublicKey, generateKeyPairSync, verify } from "node:crypto";
import { NodeFileKeyStore } from "@akb/agent-identity-sdk/keystore-node";
import { once } from "node:events";
import { Effect } from "effect";
import plugin from "../dist/index.js";
import { WorkloadClient } from "../dist/client.js";

async function fixture(transport = "mcp", version = 1) {
  const root = await mkdtemp(path.join(tmpdir(), "akb-plugin-"));
  const workspace = path.join(root, "workspace"); await mkdir(workspace);
  const pair = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const ids = ["e21cce9c-1b16-4ab8-a76a-bb761df919b8", "01965897-0976-471c-8d64-27eb682a227b"];
  const keys = [];
  if (version === 2) {
    const store = await NodeFileKeyStore.open(path.join(root, "keys"), workspace);
    for (const externalAgentId of ids) {
      const owner = { platformId: "opencode", installationId: "install", workspaceId: "workspace", externalAgentId };
      const key = await store.initialize(owner); keys.push(key); await store.selectRegisteredKey(owner, key.kid);
    }
  }
  const calls = [];
  const server = createServer(async (req, res) => {
    const buffers = []; for await (const b of req) buffers.push(b);
    const raw = Buffer.concat(buffers).toString();
    const proof = req.headers["x-akb-workload-assertion"];
    assert.ok(proof); assert.equal(req.headers["x-api-key"], "shared-test-key");
    const [header, payload, signature] = proof.split(".");
    const claims = JSON.parse(Buffer.from(payload, "base64url").toString());
    const selected = version === 2 ? keys[ids.findIndex(id => claims.sub === `agent:${id}`)] : undefined;
    assert.ok(verify("RSA-SHA256", Buffer.from(`${header}.${payload}`), selected ? createPublicKey({ key: selected.publicJwk, format: "jwk" }) : pair.publicKey, Buffer.from(signature, "base64url")));
    if (version === 2) assert.equal(claims.ver, 2);
    assert.equal(claims.request_binding.method, req.method);
    assert.equal(claims.request_binding.target, req.url);
    assert.equal(claims.request_binding.body_sha256, createHash("sha256").update(raw).digest("hex"));
    assert.equal(claims.exp - claims.iat, 60);
    const body = raw ? JSON.parse(raw) : {};
    calls.push({ claims, body, url: req.url });
    res.setHeader("Content-Type", "application/json");
    if (transport === "mcp") res.end(JSON.stringify({ jsonrpc: "2.0", id: body.id, result: body.method === "initialize"
      ? { protocolVersion: "2025-06-18" } : { content: [{ type: "text", text: claims.sub }], structuredContent: { agent: claims.sub } } }));
    else res.end(JSON.stringify({ success: true, data: req.method === "GET" ? [{ id: 42, baseCode: "PRIVATE" }] : { candidates: [{ content: claims.sub }] } }));
  });
  server.listen(0, "127.0.0.1"); await once(server, "listening");
  const config = { origin: `http://127.0.0.1:${server.address().port}`, allowInsecureLoopback: true, transport,
    serviceKeyFile: path.join(root, "service.key"), privateKeyFile: path.join(root, "private.pem"),
    issuer: "https://identity.test/opencode", audience: "akb", applicationPrincipalId: "1",
    installationId: "install", workspaceId: "workspace", workspaceRoot: workspace, agents: { build: "uuid-a", review: "uuid-b" } };
  const configFile = path.join(root, "identity.json");
  if (version === 2) Object.assign(config, { configVersion: 2, proofProfile: "AGENT_KEY_RS256_V2", platformId: "opencode",
    keyStore: { provider: "node-file", rootDirectory: path.join(root,"keys") },
    bindings: ids.map((id,i) => ({ platformId:"opencode",installationId:"install",workspaceId:"workspace",externalAgentId:id,
      policyId:"17",bindingId:String(21+i),runtimeAgentKey:i?"review":"build",applicationPrincipalId:"1",
      issuer:config.issuer,audience:"akb",resource:config.origin })) });
  await writeFile(config.serviceKeyFile, "shared-test-key", { mode: 0o600 });
  await writeFile(config.privateKeyFile, pair.privateKey.export({ format: "pem", type: "pkcs8" }), { mode: 0o600 });
  await writeFile(configFile, JSON.stringify(config), { mode: 0o600 });
  const context = (agent, ask = () => Effect.void) => ({ agent, sessionID: "ses_test", messageID: "msg_test", worktree: workspace,
    directory: workspace, abort: new AbortController().signal, ask, metadata() {} });
  return { config, configFile, context, calls, workspace, root,
    async close() { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); await rm(root, { recursive: true, force: true }); } };
}

test("two Agents share one key; concurrent MCP handshakes and tool calls carry their own fresh proofs", async () => {
  const f = await fixture();
  try {
    const hooks = await plugin.server({ directory: f.workspace }, { configFile: f.configFile });
    const [a,b] = await Promise.all([
      hooks.tool.akb_search.execute({ query: "a", agent_id: "uuid-b" }, f.context("build")),
      hooks.tool.akb_search.execute({ query: "b" }, f.context("review")),
    ]);
    assert.match(a.output, /agent:uuid-a/); assert.match(b.output, /agent:uuid-b/);
    assert.equal(f.calls.length, 4); assert.equal(new Set(f.calls.map(x => x.claims.jti)).size, 4);
    for (const call of f.calls.filter(x => x.body.method === "tools/call")) {
      assert.equal(call.body.params.arguments.agent_id, undefined);
      assert.equal(call.claims.sub, call.body.params.arguments.query === "a" ? "agent:uuid-a" : "agent:uuid-b");
    }
  } finally { await f.close(); }
});
(process.platform === "win32" ? test.skip : test)("V2 OpenCode adapter uses independent Agent keys and executes the real approval Effect", async () => {
  const f = await fixture("mcp", 2);
  try {
    const hooks = await plugin.server({ directory: f.workspace }, { configFile: f.configFile });
    let approvals = 0;
    await assert.rejects(hooks.tool.akb_search.execute({ query: "denied" }, f.context("build", () => Effect.sync(() => { approvals++; throw new Error("DENIED"); }))), /DENIED/);
    assert.equal(approvals, 1); assert.equal(f.calls.length, 0);
    const [a,b] = await Promise.all([
      hooks.tool.akb_search.execute({ query: "a", agentId: "forged" }, f.context("build")),
      hooks.tool.akb_search.execute({ query: "b" }, f.context("review")),
    ]);
    assert.notEqual(a.metadata.externalAgentId, b.metadata.externalAgentId);
    assert.equal(new Set(f.calls.map(c => c.claims.sub)).size, 2);
    assert.equal(new Set(f.calls.map(c => c.claims.jti)).size, 4);
    for (const call of f.calls.filter(c => c.body.method === "tools/call")) assert.equal(call.body.params.arguments.agentId, undefined);
  } finally { await f.close(); }
});
test("permission Effect is executed; denial prevents every upstream request", async () => {
  const f = await fixture();
  try {
    const hooks = await plugin.server({ directory: f.workspace }, { configFile: f.configFile });
    let approvals = 0;
    await assert.rejects(hooks.tool.akb_search.execute({ query: "denied" }, f.context("build", () => Effect.sync(() => { approvals++; throw new Error("DENIED"); }))), /DENIED/);
    assert.equal(approvals, 1); assert.equal(f.calls.length, 0);
    await assert.rejects(hooks.tool.akb_search.execute({ query: "unknown" }, f.context("forged")), /unregistered/);
    assert.equal(f.calls.length, 0);
  } finally { await f.close(); }
});
test("REST resolves private base code using signed discovery and sends numeric ID", async () => {
  const f = await fixture("rest");
  try {
    const hooks = await plugin.server({ directory: f.workspace }, { configFile: f.configFile });
    await hooks.tool.akb_search.execute({ query: "policy", knowledgeBaseCode: "PRIVATE" }, f.context("build"));
    assert.equal(f.calls.length, 2); assert.equal(f.calls[1].body.knowledgeBaseId, 42);
    assert.equal(f.calls[1].url, "/api/v1/external/search");
    assert.notEqual(f.calls[0].claims.jti, f.calls[1].claims.jti);
    await assert.rejects(hooks.tool.akb_search.execute({ query: "policy", knowledgeBaseCode: "PRIVATE", knowledgeBaseId: 999 }, f.context("build")), /mismatched/);
  } finally { await f.close(); }
});
test("configuration, key files and runtime workspace are protected", async () => {
  const f = await fixture();
  try {
    const client = await WorkloadClient.load(f.configFile, f.workspace);
    const other = path.join(f.root, "other"); await mkdir(other);
    await assert.rejects(client.invocation({ ...f.context("build"), directory: other }), /Unapproved/);
    if (process.platform !== "win32") {
      await chmod(f.config.privateKeyFile, 0o644);
      await assert.rejects(WorkloadClient.load(f.configFile, f.workspace), /owner-only/);
    }
    await writeFile(f.configFile, JSON.stringify({ ...f.config, origin: "http://example.com" }));
    await assert.rejects(WorkloadClient.load(f.configFile, f.workspace), /HTTPS/);
  } finally { await f.close(); }
});
test("cancelled invocations do not send a request", async () => {
  const f = await fixture();
  try {
    const hooks = await plugin.server({ directory: f.workspace }, { configFile: f.configFile });
    const controller = new AbortController(); controller.abort();
    await assert.rejects(hooks.tool.akb_search.execute({ query: "cancel" }, { ...f.context("build"), abort: controller.signal }));
    assert.equal(f.calls.length, 0);
  } finally { await f.close(); }
});
