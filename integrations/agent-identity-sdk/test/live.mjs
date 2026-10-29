/** Opt-in real AKB test. Config/cases are protected operator files, never commit credentials. */
import assert from "node:assert/strict";
import { readProtectedFile } from "../dist/keystore-node.js";

const configFile = process.env.AKB_PLUGIN_CONFIG, casesFile = process.env.AKB_LIVE_CASES;
if (!configFile || !casesFile) throw new Error("Set AKB_PLUGIN_CONFIG and AKB_LIVE_CASES to protected absolute files");
const config = JSON.parse(await readProtectedFile(configFile));
const cases = JSON.parse(await readProtectedFile(casesFile));
assert.ok(config.bindings.length >= 2, "At least two independently authorized Agents are required");
const adapter = { capture(context) { return { context, async authorize() {} }; } };
for (const transport of ["mcp", "rest"]) {
  // Construct each transport from the same protected operator configuration.
  const { createRuntime } = await import("../dist/runtime.js");
  const { NodeFileKeyStore } = await import("../dist/keystore-node.js");
  const { realpath } = await import("node:fs/promises");
  const approved = await realpath(config.workspaceRoot);
  const runtime = createRuntime({ adapter, bindings: config.bindings,
    keyStore: await NodeFileKeyStore.open(config.keyStore.rootDirectory, approved),
    credentials: { async readServiceKey() { return (await readProtectedFile(config.serviceKeyFile, approved)).trim(); } },
    origin: config.origin, transport, allowInsecureLoopback: config.allowInsecureLoopback,
    async validateWorkspace(value) { return await realpath(value) === approved; },
  });
  for (const binding of config.bindings.slice(0,2)) {
    const sample = cases[binding.runtimeAgentKey]; assert.ok(sample?.query && sample?.knowledgeBaseId);
    const context = { platformId: binding.platformId, runtimeAgentKey: binding.runtimeAgentKey, sessionId: `live-${transport}`,
      workspaceRoot: config.workspaceRoot, abortSignal: new AbortController().signal };
    await runtime.invoke(context, { operation: "bases" });
    const own = await runtime.invoke(context, { operation: "search", query: sample.query, knowledgeBaseId: sample.knowledgeBaseId, topK: 3 });
    const data = transport === "mcp" ? own.data.structuredContent : own.data;
    assert.ok(data?.candidates?.length > 0, "Own private base must return indexed content");
    const otherBinding = config.bindings.find(b => b.externalAgentId !== binding.externalAgentId), other = cases[otherBinding.runtimeAgentKey];
    await assert.rejects(runtime.invoke(context, { operation: "search", query: sample.query, knowledgeBaseId: other.knowledgeBaseId }));
    const answer = await runtime.invoke(context, { operation: "query", query: sample.query, knowledgeBaseId: sample.knowledgeBaseId });
    assert.ok(answer.data && typeof answer.data === "object");
    console.log(JSON.stringify({ transport, runtimeAgentKey: binding.runtimeAgentKey, candidates: data.candidates.length, crossBaseDenied: true }));
  }
}
