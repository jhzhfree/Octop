/** Illustrative adapter. This host interface is fictional, not an existing platform API. */
import type {
  AgentIdentityRuntime, PlatformAdapter, RuntimeDependencies, RuntimeFactory,
} from "../contracts.js";

export interface ExampleHostContext {
  readonly executingAgent: { readonly immutableKey: string };
  readonly session: { readonly id: string; readonly messageId?: string };
  readonly approvedDirectory: string;
  readonly signal: AbortSignal;
  readonly permissions: {
    request(input: { action: string; knowledgeBaseId?: number; knowledgeBaseCode?: string }): Promise<void>;
  };
}

export const exampleAdapter: PlatformAdapter<ExampleHostContext> = {
  capture(host) {
    // The real platform adapter must obtain these values from trusted host APIs.
    const context = Object.freeze({
      platformId: "example-platform",
      runtimeAgentKey: host.executingAgent.immutableKey,
      sessionId: host.session.id,
      messageId: host.session.messageId,
      workspaceRoot: host.approvedDirectory,
      abortSignal: host.signal,
    });
    const permissions = host.permissions;
    return {
      context,
      async authorize(summary) {
        context.abortSignal.throwIfAborted();
        await permissions.request({
          action: `akb_${summary.operation}`,
          knowledgeBaseId: summary.knowledgeBaseId,
          knowledgeBaseCode: summary.knowledgeBaseCode,
        });
        context.abortSignal.throwIfAborted();
      },
    };
  },
};

export function createExamplePlugin(
  createRuntime: RuntimeFactory,
  dependencies: Omit<RuntimeDependencies<ExampleHostContext>, "adapter">,
) {
  const runtime: AgentIdentityRuntime<ExampleHostContext> = createRuntime({
    ...dependencies,
    adapter: exampleAdapter,
  });
  // A real platform registers these functions through its tool SDK and validates schemas.
  return {
    bases: (host: ExampleHostContext) => runtime.invoke(host, { operation: "bases" }),
    search: (args: { query: string; knowledgeBaseId?: number }, host: ExampleHostContext) =>
      runtime.invoke(host, { operation: "search", query: args.query, knowledgeBaseId: args.knowledgeBaseId }),
  };
}
