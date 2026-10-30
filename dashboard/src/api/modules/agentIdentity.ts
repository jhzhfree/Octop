import { request } from "../request";
import type { ExternalKnowledgeChunk } from "./externalKnowledge";

export interface SourceAgentIdentity {
  agentId: string;
  name: string;
  externalAgentId: string;
  activeKid: string | null;
}
export interface SourceIdentityState {
  runtimeReady: boolean;
  agents: SourceAgentIdentity[];
}
const root = (id: string) =>
  `/external-knowledge/sources/${encodeURIComponent(id)}/identity`;
function post<T>(url: string, body: unknown = {}) {
  return request<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
export const agentIdentityApi = {
  connect: (
    id: string,
    agent: string,
    externalAgentId: string,
    privateKey: string,
  ) =>
    post<{ activeKid: string | null }>(
      `${root(id)}/agents/${encodeURIComponent(agent)}/connect`,
      { externalAgentId, privateKey },
    ),
  disconnect: (id: string, agent: string) =>
    request<{ ok: boolean }>(
      `${root(id)}/agents/${encodeURIComponent(agent)}/connect`,
      { method: "DELETE" },
    ),
  state: (id: string) => request<SourceIdentityState>(root(id)),
  search: (id: string, agent: string, query: string) =>
    post<{ chunks: ExternalKnowledgeChunk[] }>(
      `${root(id)}/agents/${encodeURIComponent(agent)}/search`,
      { query, top_k: 3 },
    ),
};
