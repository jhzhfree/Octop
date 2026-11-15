import { beforeEach, expect, it, vi } from "vitest";
import { request } from "../request";
import { reportAttachmentApi } from "./reportAttachment";
vi.mock("../request", () => ({ request: vi.fn() }));
beforeEach(() => vi.resetAllMocks());
it("uses exact encoded target and explicit version/hash sharing confirmation", () => {
  const target = { organizationId: "org/a", runId: "run/b" };
  const preview = {
    report_run_id: "report/c",
    report_version: 3,
    report_content_hash: "a".repeat(64),
    content: "not a request field",
  };
  reportAttachmentApi.preview(target, preview.report_run_id);
  expect(request).toHaveBeenCalledWith(
    "/organizations/org%2Fa/scene-runs/run%2Fb/report-attachment/preview?report_run_id=report%2Fc",
    { cache: "no-store" },
  );
  reportAttachmentApi.attach(target, preview);
  expect(request).toHaveBeenLastCalledWith(
    "/organizations/org%2Fa/scene-runs/run%2Fb/report-attachment",
    {
      method: "POST",
      body: JSON.stringify({
        report_run_id: "report/c",
        expected_report_version: 3,
        expected_content_hash: "a".repeat(64),
        share_with_reviewer: true,
      }),
    },
  );
});
