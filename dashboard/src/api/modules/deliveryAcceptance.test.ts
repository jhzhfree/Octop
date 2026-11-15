import { beforeEach, expect, it, vi } from "vitest";
import { request } from "../request";
import {
  deliveryAcceptanceApi,
  validAcceptanceReview,
  type AcceptanceReview,
} from "./deliveryAcceptance";
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

const sourceReview: AcceptanceReview = {
  run_id: "r",
  answer: "summary",
  evidence_sha256: "a".repeat(64),
  snapshot_sha256: "b".repeat(64),
  evidence_length: 7,
  acceptance: null,
};
it.each(
  [
    [],
    [
      {
        kind: "automatic_agent",
        source_run_id: "r",
        content_sha256: "a".repeat(64),
      },
    ],
    [
      {
        kind: "assistant_summary",
        source_run_id: "other",
        content_sha256: "a".repeat(64),
      },
    ],
    [
      {
        kind: "assistant_summary",
        source_run_id: "r",
        content_sha256: "c".repeat(64),
      },
    ],
    [
      {
        kind: "owner_shared_report",
        source_run_id: "r",
        content_sha256: "a".repeat(64),
      },
    ],
    [
      {
        kind: "assistant_summary",
        source_run_id: "r",
        content_sha256: ["a".repeat(64)],
      },
    ],
  ].map((sources) => ({ sources })),
)("rejects malformed or unsupported provenance %j", ({ sources }) => {
  expect(
    validAcceptanceReview(
      {
        ...sourceReview,
        evidence_sources: sources,
      } as unknown as AcceptanceReview,
      { organizationId: "o", runId: "r" },
    ),
  ).toBe(false);
});
it("accepts a same-run text fingerprint and keeps legacy provenance unknown", () => {
  expect(
    validAcceptanceReview(sourceReview, { organizationId: "o", runId: "r" }),
  ).toBe(true);
  expect(
    validAcceptanceReview(
      {
        ...sourceReview,
        evidence_sources: [
          {
            kind: "assistant_summary",
            source_run_id: "r",
            content_sha256: "a".repeat(64),
          },
        ],
      },
      { organizationId: "o", runId: "r" },
    ),
  ).toBe(true);
});
