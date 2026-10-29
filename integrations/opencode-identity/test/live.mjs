// Opt-in real AKB check. The application/policy/Agents must already be provisioned.
import assert from "node:assert/strict";
import { readFile, mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { Effect } from "effect";
import plugin from "../dist/index.js";

const configFile = process.env.AKB_PLUGIN_CONFIG;
if (!configFile) throw new Error("Set AKB_PLUGIN_CONFIG to a protected, provisioned identity JSON file");
const config = JSON.parse(await readFile(configFile, "utf8"));
const agents = config.configVersion === 2 ? config.bindings.map(binding => binding.runtimeAgentKey) : Object.keys(config.agents);
assert.ok(agents.length >= 2, "Provision two Agents with different base grants");
const temporary = await mkdtemp(path.join(tmpdir(), "akb-live-"));
const results = [];
try {
  for (const transport of ["mcp", "rest"]) {
    const filename = path.join(temporary, transport + ".json");
    await writeFile(filename, JSON.stringify({ ...config, transport }), { mode: 0o600 });
    const hooks = await plugin.server({ directory: config.workspaceRoot }, { configFile: filename });
    const invoke = async (agent, tool, args) => {
      const result = await hooks.tool[tool].execute(args, { agent, directory: config.workspaceRoot, worktree: config.workspaceRoot,
        sessionID: "ses_live", messageID: "msg_live", abort: new AbortController().signal,
        metadata() {}, ask() { return Effect.void; } });
      const parsed = JSON.parse(result.output);
      return parsed.structuredContent ?? parsed.data ?? parsed;
    };
    const [a,b] = await Promise.all(agents.slice(0,2).map(agent => invoke(agent, "akb_bases", {})));
    assert.ok(Array.isArray(a) && Array.isArray(b));
    const onlyA = a.find(base => !b.some(other => other.id === base.id));
    const onlyB = b.find(base => !a.some(other => other.id === base.id));
    assert.ok(onlyA && onlyB, "Each Agent must have at least one independently granted KB");
    for (const [agent,own,other] of [[agents[0],onlyA,onlyB],[agents[1],onlyB,onlyA]]) {
      const query = process.env.AKB_LIVE_QUERY ?? "policy 报销 deployment release approvals";
      const result = await invoke(agent, "akb_search", { query, knowledgeBaseId: own.id, topK: 3 });
      assert.ok(Array.isArray(result.candidates) && result.candidates.length > 0, "Own KB must contain indexed matching knowledge");
      await assert.rejects(invoke(agent, "akb_search", { query, knowledgeBaseId: other.id }));
      const answer = await invoke(agent, "akb_query", { query, knowledgeBaseId: own.id, topK: 3 });
      assert.ok(answer && typeof answer === "object", "Query must return a valid AKB result");
      results.push({ transport, agent, ownBase: own.id, returned: result.candidates.length, crossBaseDenied: true });
    }
  }
  console.log(JSON.stringify({ passed: true, results }, null, 2));
} finally { await rm(temporary, { recursive: true, force: true }); }
