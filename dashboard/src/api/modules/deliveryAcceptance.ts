import { request } from "../request";

export interface AcceptanceTarget {
  organizationId: string;
  runId: string;
}
export type AcceptanceDecision = "accepted" | "changes_requested";
export interface AcceptanceRecord {
  run_id: string;
  organization_id: string;
  decision: AcceptanceDecision;
  reviewer_id: number;
  reviewed_at: number;
  evidence_sha256: string;
  evidence_length: number;
  snapshot_sha256: string;
  scene_version: number;
}
export interface AcceptanceReview {
  run_id: string;
  answer: string;
  evidence_sha256: string;
  evidence_length: number;
  snapshot_sha256: string;
  acceptance: AcceptanceRecord | null;
}
const path = (target: AcceptanceTarget) =>
  `/organizations/${encodeURIComponent(
    target.organizationId,
  )}/scene-runs/${encodeURIComponent(target.runId)}/acceptance`;
export const deliveryAcceptanceApi = {
  review: (target: AcceptanceTarget) =>
    request<AcceptanceReview>(`${path(target)}/review`, { cache: "no-store" }),
  decide: (
    target: AcceptanceTarget,
    decision: AcceptanceDecision,
    review: AcceptanceReview,
  ) =>
    request<AcceptanceRecord>(path(target), {
      method: "POST",
      body: JSON.stringify({
        decision,
        expected_evidence_sha256: review.evidence_sha256,
        expected_snapshot_sha256: review.snapshot_sha256,
      }),
    }),
};
export function validAcceptanceRecord(
  record: AcceptanceRecord,
  target: AcceptanceTarget,
) {
  return (
    record.run_id === target.runId &&
    record.organization_id === target.organizationId &&
    ["accepted", "changes_requested"].includes(record.decision) &&
    Number.isSafeInteger(record.reviewer_id) &&
    Number.isFinite(record.reviewed_at)
  );
}
export function validAcceptanceReview(
  review: AcceptanceReview,
  target: AcceptanceTarget,
) {
  return (
    review.run_id === target.runId &&
    typeof review.answer === "string" &&
    review.answer.trim().length > 0 &&
    /^[a-f0-9]{64}$/.test(review.evidence_sha256) &&
    /^[a-f0-9]{64}$/.test(review.snapshot_sha256) &&
    (review.acceptance === null ||
      (validAcceptanceRecord(review.acceptance, target) &&
        review.acceptance.evidence_sha256 === review.evidence_sha256 &&
        review.acceptance.snapshot_sha256 === review.snapshot_sha256))
  );
}
