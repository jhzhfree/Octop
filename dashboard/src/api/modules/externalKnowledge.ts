import { request } from "../request";

export interface ExternalKnowledgeSource {
  source_id: string;
  display_name: string;
  kind: string;
  endpoint: string;
  ref: string;
  top_k: number;
  min_score: number;
  timeout_ms: number;
  allow_insecure_http: boolean;
  selector_argument: string;
  forbidden_arguments: string[];
  read_tools: string[];
  enabled: boolean;
  shared: boolean;
  owner_user_id: number;
  instance_id: string;
  has_credentials: boolean;
  created_at: number;
  updated_at: number;
  can_manage?: boolean;
  bindings?: { target_type: string; target_id: string }[];
}

export interface ExternalKnowledgeChunk {
  id: string;
  content: string;
  score: number;
  source: string;
  metadata: Record<string, unknown>;
  origin: string;
  ref: string;
}

export interface ExternalKnowledgeWarning {
  origin: string;
  code: string;
  message: string;
}

export interface ExternalKnowledgeProbe {
  ok: boolean;
  code: string;
  message: string;
  tools: string[];
  refs: { ref: string; name: string }[];
}

export interface ExternalKnowledgeBinding {
  source_id: string;
  target_type: string;
  target_id: string;
  enabled: boolean;
}

export interface ExternalSourceCreateBody {
  kind: string;
  display_name: string;
  endpoint: string;
  api_key?: string;
  instance_id?: string;
  ref?: string;
  top_k?: number;
  min_score?: number;
  timeout_ms?: number;
  allow_insecure_http?: boolean;
  selector_argument?: string;
  forbidden_arguments?: string[];
  read_tools?: string[];
  shared?: boolean;
}

export type ExternalSourcePatchBody = Partial<ExternalSourceCreateBody> & {
  enabled?: boolean;
};

export const externalKnowledgeApi = {
  kinds: () => request<{ kinds: string[] }>("/external-knowledge/kinds"),

  list: () => request<ExternalKnowledgeSource[]>("/external-knowledge/sources"),

  create: (body: ExternalSourceCreateBody) =>
    request<ExternalKnowledgeSource>("/external-knowledge/sources", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  update: (sourceId: string, body: ExternalSourcePatchBody) =>
    request<ExternalKnowledgeSource>(
      `/external-knowledge/sources/${sourceId}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    ),

  remove: (sourceId: string) =>
    request<void>(`/external-knowledge/sources/${sourceId}`, {
      method: "DELETE",
    }),

  probe: (sourceId: string) =>
    request<ExternalKnowledgeProbe>(
      `/external-knowledge/sources/${sourceId}/probe`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      },
    ),

  refs: (sourceId: string) =>
    request<{ refs: { ref: string; name: string }[] }>(
      `/external-knowledge/sources/${sourceId}/refs`,
    ),

  search: (sourceId: string, query: string, topK = 0) =>
    request<{
      source_id: string;
      chunks: ExternalKnowledgeChunk[];
      warnings: ExternalKnowledgeWarning[];
      trace_ids: string[];
    }>(`/external-knowledge/sources/${sourceId}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, top_k: topK }),
    }),

  bindings: (sourceId: string) =>
    request<ExternalKnowledgeBinding[]>(
      `/external-knowledge/sources/${sourceId}/bindings`,
    ),

  bind: (
    sourceId: string,
    targetType: string,
    targetId: string,
    enabled = true,
  ) =>
    request<ExternalKnowledgeBinding>(
      `/external-knowledge/sources/${sourceId}/bindings`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          target_type: targetType,
          target_id: targetId,
          enabled,
        }),
      },
    ),

  unbind: (sourceId: string, targetType: string, targetId: string) =>
    request<void>(
      `/external-knowledge/sources/${sourceId}/bindings/${targetType}/${targetId}`,
      { method: "DELETE" },
    ),

  retrieve: (
    query: string,
    targetType: string,
    targetIds: string[],
    limit = 8,
  ) =>
    request<{
      chunks: ExternalKnowledgeChunk[];
      warnings: ExternalKnowledgeWarning[];
    }>("/external-knowledge/retrieve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        target_type: targetType,
        target_ids: targetIds,
        limit,
      }),
    }),
};
