import { request } from "../request";
import type { AcceptanceTarget } from "./deliveryAcceptance";

export interface ReportPreview {
  report_run_id: string;
  report_version: number;
  report_content_hash: string;
  content: string;
}
export interface ReportAttachment extends ReportPreview {
  run_id: string;
  organization_id: string;
  attachment_sha256: string;
  attached_by: number;
  attached_at: number;
}
const path = (target: AcceptanceTarget) =>
  `/organizations/${encodeURIComponent(
    target.organizationId,
  )}/scene-runs/${encodeURIComponent(target.runId)}/report-attachment`;
export const reportAttachmentApi = {
  get: (target: AcceptanceTarget) =>
    request<ReportAttachment | null>(path(target), { cache: "no-store" }),
  preview: (target: AcceptanceTarget, reportId: string) =>
    request<ReportPreview>(
      `${path(target)}/preview?report_run_id=${encodeURIComponent(reportId)}`,
      { cache: "no-store" },
    ),
  attach: (target: AcceptanceTarget, preview: ReportPreview) =>
    request<ReportAttachment>(path(target), {
      method: "POST",
      body: JSON.stringify({
        report_run_id: preview.report_run_id,
        expected_report_version: preview.report_version,
        expected_content_hash: preview.report_content_hash,
        share_with_reviewer: true,
      }),
    }),
};
export const validReportPreview = (value: ReportPreview, reportId: string) =>
  value.report_run_id === reportId &&
  Number.isSafeInteger(value.report_version) &&
  value.report_version > 0 &&
  /^[a-f0-9]{64}$/.test(value.report_content_hash) &&
  typeof value.content === "string" &&
  value.content.trim().length > 0;
export const validReportAttachment = (
  value: ReportAttachment,
  target: AcceptanceTarget,
) =>
  value.run_id === target.runId &&
  value.organization_id === target.organizationId &&
  validReportPreview(value, value.report_run_id) &&
  /^[a-f0-9]{64}$/.test(value.attachment_sha256);
