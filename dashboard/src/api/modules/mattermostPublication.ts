import { request } from "../request";
import type { AcceptanceTarget } from "./deliveryAcceptance";

export type PublicationStatus =
  | "pending_approval"
  | "blocked_not_connected"
  | "revoked"
  | "unknown";
export interface MattermostPublication {
  organization_id: string;
  run_id: string;
  worker_id: string;
  worker_version: number;
  intent_revision: number;
  configuration_revision: number;
  instance_key: string;
  team_id: string;
  channel_id: string;
  bot_id: string;
  report_sha256: string;
  publication_sha256: string;
  content: string;
  proposed_by: number;
  approved_by: number | null;
  status: PublicationStatus;
  created_at: number;
  updated_at: number;
  connected: false;
  dispatch_enabled: false;
  execution_authorized: false;
}
export interface PublicationCandidate {
  expected_report_sha256: string;
  expected_worker_version: number;
  expected_intent_revision: number;
  expected_configuration_revision: number;
}
const path = (target: AcceptanceTarget) =>
  `/organizations/${encodeURIComponent(
    target.organizationId,
  )}/scene-runs/${encodeURIComponent(target.runId)}/mattermost-publication`;
export const mattermostPublicationApi = {
  candidate: (target: AcceptanceTarget) =>
    request<PublicationCandidate>(`${path(target)}/candidate`, {
      cache: "no-store",
    }),
  create: (target: AcceptanceTarget, candidate: PublicationCandidate) =>
    request<MattermostPublication>(path(target), {
      method: "POST",
      cache: "no-store",
      body: JSON.stringify({
        expected_report_sha256: candidate.expected_report_sha256,
        expected_worker_version: candidate.expected_worker_version,
        expected_intent_revision: candidate.expected_intent_revision,
        expected_configuration_revision:
          candidate.expected_configuration_revision,
      }),
    }),
  get: (target: AcceptanceTarget) =>
    request<MattermostPublication>(path(target), { cache: "no-store" }),
  decide: (
    target: AcceptanceTarget,
    decision: "approve" | "revoke",
    digest: string,
  ) =>
    request<MattermostPublication>(`${path(target)}/${decision}`, {
      method: "POST",
      cache: "no-store",
      body: JSON.stringify({ expected_publication_sha256: digest }),
    }),
};
const positive = (value: number) => Number.isSafeInteger(value) && value > 0;
const hash = (value: string) =>
  typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
/** Fail closed before rendering any body or enabling a decision. */
export function validPublication(
  value: MattermostPublication,
  target: AcceptanceTarget,
) {
  return (
    value != null &&
    value.organization_id === target.organizationId &&
    value.run_id === target.runId &&
    typeof value.worker_id === "string" &&
    value.worker_id.length > 0 &&
    positive(value.worker_version) &&
    positive(value.intent_revision) &&
    positive(value.configuration_revision) &&
    positive(value.proposed_by) &&
    (value.approved_by === null || positive(value.approved_by)) &&
    /^[a-z][a-z0-9-]{0,62}$/.test(value.instance_key) &&
    [value.team_id, value.channel_id, value.bot_id].every(
      (id) => typeof id === "string" && /^[a-z0-9]{26}$/.test(id),
    ) &&
    hash(value.report_sha256) &&
    hash(value.publication_sha256) &&
    typeof value.content === "string" &&
    [
      "pending_approval",
      "blocked_not_connected",
      "revoked",
      "unknown",
    ].includes(value.status) &&
    (value.status === "revoked" || value.content.trim().length > 0) &&
    (value.status !== "blocked_not_connected" ||
      (value.approved_by !== null &&
        value.approved_by !== value.proposed_by)) &&
    Number.isFinite(value.created_at) &&
    Number.isFinite(value.updated_at) &&
    value.connected === false &&
    value.dispatch_enabled === false &&
    value.execution_authorized === false
  );
}

export function validPublicationCandidate(value: PublicationCandidate) {
  return (
    value != null &&
    hash(value.expected_report_sha256) &&
    positive(value.expected_worker_version) &&
    positive(value.expected_intent_revision) &&
    positive(value.expected_configuration_revision)
  );
}
