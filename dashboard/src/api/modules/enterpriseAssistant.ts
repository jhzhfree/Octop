import { request } from "../request";

export type WorkKind = "inspection" | "jira" | "oa" | "report" | "vmware";
export type ConnectionKind = Exclude<WorkKind, "report"> | "wecom";
export interface Features {
  enabled: boolean;
  inspection: boolean;
  work_items: boolean;
  reports: boolean;
  vmware: boolean;
  allowed_origins: string[];
  allow_http: boolean;
}
export interface ConnectionConfig {
  name: string;
  kind: ConnectionKind;
  url: string;
  inspection_transport?: "http" | "ssh";
  ssh_host_key?: string;
  username?: string;
  auth?: string;
  jira_api?: string;
  jql?: string;
  username_selector?: string;
  password_selector?: string;
  login_selector?: string;
  ready_selector?: string;
  items_selector?: string;
  template_id?: string;
  os_label?: string;
  folder?: string;
  resource_pool?: string;
  datastore?: string;
  customization_spec?: string;
}
export interface JobConfig {
  name: string;
  kind: WorkKind;
  agent_id: string;
  connection_id: string;
  source_job_ids: string[];
  enabled: boolean;
  cron: string;
  timezone: string;
  vm_name: string;
}
export interface Resource<C> {
  id: string;
  version: number;
  config: C;
  has_secret: boolean;
  next_due: number | null;
}
export interface WorkRun {
  id: string;
  job_id: string;
  status: string;
  version: number;
  snapshot: { job: JobConfig; connection: ConnectionConfig };
  evidence: Record<string, unknown>[];
  content: string;
  content_hash: string;
  error_code: string;
  created_at: number;
  updated_at: number;
}
const base = "/enterprise-assistant";
const json = (method: string, body: unknown) => ({
  method,
  body: JSON.stringify(body),
});
export const enterpriseAssistantApi = {
  features: () => request<Features>(`${base}/features`),
  setFeatures: (body: Features) =>
    request<Features>(`${base}/features`, json("PUT", body)),
  connections: () =>
    request<Resource<ConnectionConfig>[]>(`${base}/connections`),
  jobs: () => request<Resource<JobConfig>[]>(`${base}/jobs`),
  runs: () => request<WorkRun[]>(`${base}/runs`),
  saveConnection: (
    config: ConnectionConfig,
    secret: string,
    old?: Resource<ConnectionConfig>,
  ) =>
    request<Resource<ConnectionConfig>>(
      `${base}/connections${old ? `/${old.id}` : ""}`,
      json(old ? "PUT" : "POST", {
        config,
        secret,
        expected_version: old?.version,
      }),
    ),
  saveJob: (config: JobConfig, old?: Resource<JobConfig>) =>
    request<Resource<JobConfig>>(
      `${base}/jobs${old ? `/${old.id}` : ""}`,
      json(old ? "PUT" : "POST", { config, expected_version: old?.version }),
    ),
  run: (id: string) =>
    request<WorkRun>(`${base}/jobs/${id}/runs`, { method: "POST" }),
  editDraft: (run: WorkRun, content: string) =>
    request<WorkRun>(
      `${base}/runs/${run.id}/draft`,
      json("PUT", { expected_version: run.version, content }),
    ),
  decide: (run: WorkRun, approve: boolean) =>
    request<WorkRun>(
      `${base}/runs/${run.id}/decision`,
      json("POST", {
        expected_version: run.version,
        content_hash: run.content_hash,
        approve,
      }),
    ),
};
