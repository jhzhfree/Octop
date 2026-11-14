import { request } from "../request";

export interface WorkerKnowledgeMapping {
  mapping_id: string;
  organization_id: string;
  worker_id: string;
  capability_json: string;
  external_source_id: string;
  capability_version: number;
  connection_revision: number;
  resource_revision: number;
  revision: number;
  status: "draft" | "revoked";
  local_snapshot_current: boolean | null;
  retrieval_enabled: false;
  authorization_status: "delegated_user_acl_unverified";
}
export interface MappingCreate {
  mcp_server: string;
  knowledge_base_ref: string;
  external_source_id: string;
  expected_capability_version: number;
  expected_connection_revision: number;
  expected_resource_revision: number;
}
const base = (org: string, worker: string) =>
  `/organizations/${encodeURIComponent(
    org,
  )}/digital-workers/${encodeURIComponent(worker)}/knowledge-mappings`;
export const workerKnowledgeMappingsApi = {
  list: (org: string, worker: string, offset = 0, limit = 10) =>
    request<{ items: WorkerKnowledgeMapping[]; limit: number; offset: number }>(
      `${base(org, worker)}?limit=${limit}&offset=${offset}`,
    ),
  inspect: (org: string, worker: string, id: string) =>
    request<WorkerKnowledgeMapping>(
      `${base(org, worker)}/${encodeURIComponent(id)}`,
    ),
  create: (org: string, worker: string, body: MappingCreate) =>
    request<WorkerKnowledgeMapping>(base(org, worker), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  revoke: (
    org: string,
    worker: string,
    id: string,
    expected_revision: number,
  ) =>
    request<WorkerKnowledgeMapping>(
      `${base(org, worker)}/${encodeURIComponent(id)}/revoke`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_revision }),
      },
    ),
};
