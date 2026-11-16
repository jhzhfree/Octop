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
  review_note?: string;
  reviewer_id: number;
  reviewed_at: number;
  evidence_sha256: string;
  evidence_length: number;
  snapshot_sha256: string;
  scene_version: number;
}
export interface EvidenceSource {
  kind: "same_run_http_report" | "assistant_summary" | "owner_shared_report";
  source_run_id: string;
  content_sha256: string;
}
export interface AcceptanceReview {
  run_id: string;
  answer: string;
  evidence_sha256: string;
  evidence_length: number;
  snapshot_sha256: string;
  acceptance: AcceptanceRecord | null;
  evidence_sources?: EvidenceSource[];
}
const path = (target: AcceptanceTarget) =>
  `/organizations/${encodeURIComponent(
    target.organizationId,
  )}/scene-runs/${encodeURIComponent(target.runId)}/acceptance`;
export const deliveryAcceptanceApi = {
  feedback: (target: AcceptanceTarget) =>
    request<AcceptanceRecord | null>(path(target), { cache: "no-store" }),
  review: (target: AcceptanceTarget) =>
    request<AcceptanceReview>(`${path(target)}/review`, { cache: "no-store" }),
  decide: (
    target: AcceptanceTarget,
    decision: AcceptanceDecision,
    review: AcceptanceReview,
    reviewNote = "",
  ) =>
    request<AcceptanceRecord>(path(target), {
      method: "POST",
      body: JSON.stringify({
        decision,
        review_note: reviewNote,
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
    (record.review_note === undefined ||
      (typeof record.review_note === "string" &&
        Array.from(record.review_note).length <= 2000)) &&
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
    (review.evidence_sources === undefined ||
      (Array.isArray(review.evidence_sources) &&
        review.evidence_sources.length >= 1 &&
        review.evidence_sources.length <= 2 &&
        review.evidence_sources.every(
          (source, index) =>
            !!source &&
            typeof source.source_run_id === "string" &&
            !!source.source_run_id &&
            typeof source.content_sha256 === "string" &&
            /^[a-f0-9]{64}$/.test(source.content_sha256) &&
            (index === 0
              ? ["same_run_http_report", "assistant_summary"].includes(
                  source.kind,
                ) && source.source_run_id === target.runId
              : source.kind === "owner_shared_report"),
        ) &&
        (review.evidence_sources.length !== 1 ||
          review.evidence_sources[0].content_sha256 ===
            review.evidence_sha256))) &&
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
