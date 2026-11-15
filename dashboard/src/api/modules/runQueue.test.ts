import { beforeEach, expect, it, vi } from "vitest";
import { request } from "../request";
import { runQueueApi, validRunQueue, type RunQueue } from "./runQueue";
vi.mock("../request", () => ({ request: vi.fn() }));
beforeEach(() => vi.mocked(request).mockReset());
const accepted: RunQueue = {
  organization_id: "o",
  category: "accepted",
  as_of: 100,
  items: [
    { run_id: "r", status: "succeeded", created_at: 90, decision: "accepted" },
  ],
  next_before: null,
  execution_authorized: false,
  persisted_state_may_lag: true,
};
it("reads the accepted category with encoded organization, cursor and no cache", async () => {
  await runQueueApi.read("o/a", "accepted", 20);
  expect(request).toHaveBeenCalledWith(
    "/organizations/o%2Fa/run-queue?category=accepted&limit=20&before=20",
    { cache: "no-store" },
  );
});
it("requires a persisted human acceptance and successful execution", () => {
  expect(validRunQueue(accepted, "o", "accepted")).toBe(true);
  for (const decision of [null, "changes_requested"] as const) {
    expect(
      validRunQueue(
        { ...accepted, items: [{ ...accepted.items[0], decision }] },
        "o",
        "accepted",
      ),
    ).toBe(false);
  }
  expect(
    validRunQueue(
      { ...accepted, items: [{ ...accepted.items[0], status: "failed" }] },
      "o",
      "accepted",
    ),
  ).toBe(false);
  expect(validRunQueue(accepted, "other", "accepted")).toBe(false);
  expect(validRunQueue(accepted, "o", "unreviewed_success")).toBe(false);
});
