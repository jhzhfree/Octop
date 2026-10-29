import type { Plugin } from "@opencode-ai/plugin";
import { tool } from "@opencode-ai/plugin/tool";
import { Effect, Fiber } from "effect";
import { WorkloadClient, readIdentityConfiguration, type Operation } from "./client.js";
import type { ToolContext } from "@opencode-ai/plugin/tool";
import type { PlatformAdapter } from "@akb/agent-identity-sdk/types";
import { loadNodeRuntime } from "@akb/agent-identity-sdk/node";

export const openCodeAdapter: PlatformAdapter<ToolContext> = {
  capture(context) {
    const runtimeContext = Fiber.getCurrent()?.context;
    const execution = Object.freeze({ platformId: "opencode", runtimeAgentKey: context.agent, sessionId: context.sessionID,
      messageId: context.messageID, workspaceRoot: context.directory, abortSignal: context.abort });
    return { context: execution, async authorize(summary) {
      const approval = context.ask({ permission: `akb_${summary.operation}`, patterns: ["*"], always: ["*"],
        metadata: { operation: summary.operation, knowledgeBaseId: summary.knowledgeBaseId, knowledgeBaseCode: summary.knowledgeBaseCode } });
      await Effect.runPromise(runtimeContext ? approval.pipe(Effect.provide(runtimeContext)) : approval, { signal: context.abort });
    } };
  },
};

const plugin: Plugin = async (input, options) => {
  if (typeof options?.configFile !== "string") throw new Error("Set the protected AKB configFile in global plugin options");
  const config = await readIdentityConfiguration(options.configFile, input.directory);
  if (config.configVersion !== undefined && config.configVersion !== 2) throw new Error("Unsupported identity configuration version");
  if (config.proofProfile && config.proofProfile !== "AGENT_KEY_RS256_V2") throw new Error("Unsupported explicit identity profile");
  if (config.proofProfile && config.configVersion !== 2) throw new Error("V2 profile requires V2 configuration");
  const runtime = config.configVersion === 2 ? await loadNodeRuntime(options.configFile, input.directory, openCodeAdapter) : undefined;
  const client = runtime ? undefined : await WorkloadClient.load(options.configFile, input.directory);
  const filters = {
    query: tool.schema.string().min(1).max(4000),
    topK: tool.schema.number().int().min(1).max(50).optional(),
    knowledgeBaseCode: tool.schema.string().min(1).max(128).optional(),
    knowledgeBaseId: tool.schema.number().int().positive().optional(),
  };
  const make = (operation: Operation) => tool({
    description: operation === "bases" ? "List AKB bases authorized for the executing Agent" : `AKB ${operation} using the executing Agent's verified identity`,
    args: operation === "bases" ? {} : filters,
    async execute(args, context) {
      if (runtime) {
        const result = await runtime.invoke(context, operation === "bases" ? { operation: "bases" } : {
          operation, query: args.query!, topK: args.topK, knowledgeBaseId: args.knowledgeBaseId, knowledgeBaseCode: args.knowledgeBaseCode,
        });
        return { output: JSON.stringify(result.data), metadata: result.metadata };
      }
      // Capture the host fiber before any await: ctx.ask is an Effect in 1.14.30.
      const fiber = Fiber.getCurrent();
      const runtimeContext = fiber?.context;
      const invocation = await client!.invocation(context);
      const approval = context.ask({ permission: `akb_${operation}`, patterns: ["*"], always: ["*"], metadata: { operation } });
      await Effect.runPromise(runtimeContext ? approval.pipe(Effect.provide(runtimeContext)) : approval, { signal: context.abort });
      context.abort.throwIfAborted();
      const result = await client!.execute(operation, args, invocation, context.abort);
      return { output: JSON.stringify(result), metadata: { invocationId: invocation.invocationId, agent: invocation.runtimeAgent } };
    },
  });
  return { tool: { akb_search: make("search"), akb_query: make("query"), akb_bases: make("bases") } };
};

export default { id: "akb-opencode-identity", server: plugin };
