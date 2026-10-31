import { request } from "../request";
import type { ExternalKnowledgeChunk } from "./externalKnowledge";

export interface SourceAgentIdentity {
  agentId: string;
  name: string;
  externalAgentId: string;
  activeKid: string | null;
  identityVerified?: boolean;
  /** Saved public key, safe to show and copy. The private half is never returned. */
  publicJwk?: Record<string, string> | null;
  keySaved?: boolean;
  lastVerifiedAt?: string | null;
  lastVerificationStatus?: "success" | "failure" | null;
}
export interface SourceIdentityState {
  connectionId?: string;
  revision?: number;
  resourceCount?: number;
  resources?: { sourceId: string; name: string }[];
  pendingKeys?: GeneratedAgentKey[];
  runtimeReady: boolean;
  /** Native identity runtime diagnostic, when unavailable. */
  runtimeReason?: string | null;
  serviceKeySaved?: boolean;
  agents: SourceAgentIdentity[];
}
const root = (id: string) =>
  id.startsWith("connection:")
    ? `/external-knowledge/connections/${encodeURIComponent(
        id.slice(11),
      )}/identity`
    : `/external-knowledge/sources/${encodeURIComponent(id)}/identity`;
function post<T>(url: string, body: unknown = {}) {
  return request<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
export interface GeneratedAgentKey {
  externalAgentId: string;
  kid: string;
  publicJwk: Record<string, string>;
  keyReference: string;
}
export const agentIdentityApi = {
  generate: (id: string, externalAgentId: string) =>
    post<GeneratedAgentKey>(`${root(id)}/generate`, { externalAgentId }),
  connect: (
    id: string,
    agent: string,
    externalAgentId: string,
    privateKey: string,
    keyReference = "",
    revision?: number,
  ) =>
    post<{ activeKid: string | null }>(
      `${root(id)}/agents/${encodeURIComponent(agent)}/connect`,
      {
        externalAgentId,
        privateKey,
        keyReference,
        revision,
        mountResource: false,
      },
    ),
  disconnect: (id: string, agent: string, revision?: number) =>
    request<{ ok: boolean }>(
      `${root(id)}/agents/${encodeURIComponent(agent)}/connect${
        revision ? `?revision=${revision}` : ""
      }`,
      { method: "DELETE" },
    ),
  discard: (id: string, reference: string) =>
    request<{ ok: boolean }>(
      `${root(id)}/generated/${encodeURIComponent(reference)}`,
      { method: "DELETE" },
    ),
  state: (id: string) => request<SourceIdentityState>(root(id)),
  /** Knowledge bases a bound Agent may read. Identity-bound sources reject the
   *  legacy credential calls, so the picklist travels through the binding. */
  bases: (id: string, agent: string) =>
    request<{ refs: { ref: string; name: string }[] }>(
      `${root(id)}/agents/${encodeURIComponent(agent)}/bases`,
    ),
  search: (id: string, agent: string, query: string) =>
    post<{ chunks: ExternalKnowledgeChunk[] }>(
      `${root(id)}/agents/${encodeURIComponent(agent)}/search`,
      { query, top_k: 3 },
    ),
};
