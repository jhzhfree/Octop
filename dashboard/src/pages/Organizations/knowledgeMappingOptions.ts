import type { ExternalKnowledgeSource } from "../../api/modules/externalKnowledge";
export interface MappingCapability {
  mcp_server: string;
  knowledge_base_ref: string;
}
/** Display existing declared capabilities only; never infer permission from names. */
export function mappingCapabilities(value: unknown): MappingCapability[] {
  if (!value || typeof value !== "object") return [];
  const c = value as Record<string, unknown>;
  const current = c.knowledge_sources;
  const raw =
    Array.isArray(current) && current.length
      ? current
      : c.akb_knowledge_sources;
  if (!Array.isArray(raw)) return [];
  const result: MappingCapability[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object" || item.provider !== "akb") continue;
    const ref = item.knowledge_base_ref || item.knowledge_base_code;
    if (
      typeof item.mcp_server === "string" &&
      item.mcp_server.trim() &&
      typeof ref === "string" &&
      ref.trim()
    )
      result.push({
        mcp_server: item.mcp_server.trim(),
        knowledge_base_ref: ref.trim(),
      });
  }
  return result.filter(
    (item, index) =>
      result.findIndex(
        (r) =>
          r.mcp_server === item.mcp_server &&
          r.knowledge_base_ref === item.knowledge_base_ref,
      ) === index,
  );
}
export function mappingSourceOptions(
  sources: ExternalKnowledgeSource[],
  agentId: string,
  ref: string,
): ExternalKnowledgeSource[] {
  return sources.filter(
    (s) =>
      ["akb-mcp", "akb-rest"].includes(s.kind) &&
      s.auth_type === "agent" &&
      s.enabled &&
      s.connection_enabled === true &&
      s.ref === ref &&
      Number.isInteger(s.revision) &&
      s.revision! > 0 &&
      Number.isInteger(s.resource_revision) &&
      s.resource_revision! > 0 &&
      s.bindings?.some(
        (b) => b.target_type === "agent" && b.target_id === agentId,
      ),
  );
}
