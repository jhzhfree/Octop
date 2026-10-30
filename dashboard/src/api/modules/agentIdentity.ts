import { request } from "../request";
import type { ExternalKnowledgeChunk } from "./externalKnowledge";

export interface AgentPublicKey {
  kid: string;
  publicJwk: {
    kty: string;
    n: string;
    e: string;
    kid: string;
    alg: string;
    use: string;
  };
  owner: {
    platformId: string;
    installationId: string;
    workspaceId: string;
    externalAgentId: string;
  };
  localState: string;
  createdAt: string;
}
export interface SourceAgentIdentity {
  agentId: string;
  name: string;
  externalAgentId: string;
  bindingId: string;
  activeKid: string | null;
  keys: AgentPublicKey[];
  unavailable?: boolean;
}
export interface IdentityConfiguration {
  mode: "LEGACY" | "AGENT_KEY_RS256_V2";
  policyId: string;
  applicationPrincipalId: string;
  issuer: string;
  audience: string;
}
export interface SourceIdentityState extends IdentityConfiguration {
  platformId: string;
  installationId: string;
  workspaceId: string;
  resource: string;
  runtimeReady: boolean;
  sdkVersion: string;
  agents: SourceAgentIdentity[];
}
export interface VerifiedAgentStatus {
  agentId: string;
  agentPrincipalId: number;
  applicationPrincipalId: number;
  policyId: number;
  kid: string;
  proofProfile: string;
  keyStatus: string;
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
  state: (id: string) => request<SourceIdentityState>(root(id)),
  configure: (id: string, body: IdentityConfiguration) =>
    request<{ ok: boolean }>(root(id), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  prepare: (id: string, agent: string, rotate = false) =>
    post(`${root(id)}/agents/${encodeURIComponent(agent)}/prepare`, { rotate }),
  prove: (
    id: string,
    agent: string,
    kid: string,
    challenge: Record<string, unknown>,
  ) =>
    post<{ registrationProof: string }>(
      `${root(id)}/agents/${encodeURIComponent(agent)}/proof`,
      { kid, challenge },
    ),
  activate: (id: string, agent: string, kid: string) =>
    post<{ status: VerifiedAgentStatus }>(
      `${root(id)}/agents/${encodeURIComponent(agent)}/activate`,
      { kid },
    ),
  probe: (id: string, agent: string) =>
    post<{ status: VerifiedAgentStatus }>(
      `${root(id)}/agents/${encodeURIComponent(agent)}/probe`,
    ),
  search: (id: string, agent: string, query: string) =>
    post<{ chunks: ExternalKnowledgeChunk[] }>(
      `${root(id)}/agents/${encodeURIComponent(agent)}/search`,
      { query, top_k: 3 },
    ),
};
