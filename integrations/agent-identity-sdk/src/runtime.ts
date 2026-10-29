import { AkbKnowledgeClient, validateRequest } from "./client.js";
import { canonicalOrigin, identifier, IdentityError, ownerIndex, ownerOf, traceId } from "./core.js";
import type { AgentIdentityRuntime, KnowledgeRequest, RuntimeDependencies } from "./types.js";
export type * from "./types.js";
export { IdentityError } from "./core.js";

export function createRuntime<NativeContext>(dependencies: RuntimeDependencies<NativeContext>): AgentIdentityRuntime<NativeContext> {
  const bindings = Object.freeze(dependencies.bindings.map(value => Object.freeze({ ...value })));
  const keys = new Set<string>(), origin = canonicalOrigin(dependencies.origin, dependencies.allowInsecureLoopback);
  for (const binding of bindings) {
    ownerIndex(binding);
    if (!identifier(binding.runtimeAgentKey) || !/^\d+$/.test(binding.policyId) || !/^\d+$/.test(binding.bindingId)
      || !/^\d+$/.test(binding.applicationPrincipalId) || !traceId(binding.audience) || typeof binding.issuer !== "string"
      || binding.issuer.length > 255 || binding.resource !== origin) throw new IdentityError("AGENT_UNREGISTERED", "Invalid approved identity binding");
    const key = `${binding.platformId}\0${binding.runtimeAgentKey}`;
    if (keys.has(key)) throw new IdentityError("AGENT_UNREGISTERED", "Ambiguous runtime Agent binding");
    keys.add(key);
  }
  const client = new AkbKnowledgeClient(dependencies.credentials, origin, dependencies.transport, dependencies.timeoutMs, dependencies.allowInsecureLoopback);
  return {
    async invoke(native: NativeContext, request: KnowledgeRequest) {
      // This must execute on the host fiber, before the first await.
      const captured = dependencies.adapter.capture(native);
      const context = Object.freeze({ ...captured.context }), input = validateRequest(request);
      const binding = bindings.find(b => b.platformId === context.platformId && b.runtimeAgentKey === context.runtimeAgentKey);
      if (!binding || !traceId(context.sessionId) || (context.messageId !== undefined && !traceId(context.messageId))) throw new IdentityError("AGENT_UNREGISTERED", "Missing or unregistered runtime Agent identity");
      context.abortSignal.throwIfAborted();
      if (!await dependencies.validateWorkspace(context.workspaceRoot)) throw new IdentityError("WORKSPACE_UNAPPROVED", "Unapproved execution workspace");
      const invocation = Object.freeze({ sessionId: context.sessionId, messageId: context.messageId, invocationId: crypto.randomUUID() });
      await captured.authorize(input.operation === "bases" ? { operation: "bases" } : { operation: input.operation,
        knowledgeBaseId: input.knowledgeBaseId, knowledgeBaseCode: input.knowledgeBaseCode });
      context.abortSignal.throwIfAborted();
      const signer = await dependencies.keyStore.openActiveSigner(ownerOf(binding));
      const data = await client.execute(input, binding, invocation, signer, context.abortSignal);
      return { data, metadata: { invocationId: invocation.invocationId, externalAgentId: binding.externalAgentId, runtimeAgentKey: binding.runtimeAgentKey } };
    },
  };
}
