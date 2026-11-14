import { beforeEach, expect, it, vi } from "vitest";
import { request } from "../request";
import { deliveryAcceptanceApi } from "./deliveryAcceptance";
vi.mock("../request", () => ({ request: vi.fn() }));
beforeEach(() => vi.mocked(request).mockReset());
it("encodes paths, disables read cache, posts only decision and two hashes", async () => {
  const target = { organizationId: "o/a", runId: "r?#" };
  await deliveryAcceptanceApi.review(target);
  expect(request).toHaveBeenLastCalledWith(
    "/organizations/o%2Fa/scene-runs/r%3F%23/acceptance/review",
    { cache: "no-store" },
  );
  await deliveryAcceptanceApi.decide(target, "changes_requested", {
    run_id: "r?#",
    answer: "private",
    evidence_sha256: "a".repeat(64),
    snapshot_sha256: "b".repeat(64),
    evidence_length: 7,
    acceptance: null,
  });
  expect(request).toHaveBeenLastCalledWith(
    "/organizations/o%2Fa/scene-runs/r%3F%23/acceptance",
    {
      method: "POST",
      body: JSON.stringify({
        decision: "changes_requested",
        expected_evidence_sha256: "a".repeat(64),
        expected_snapshot_sha256: "b".repeat(64),
      }),
    },
  );
});
