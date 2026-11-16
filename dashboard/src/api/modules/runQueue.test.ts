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

it.each(["queued", "rejected"] as const)(
  "validates %s without inventing acceptance",
  async (category) => {
    const data: RunQueue = {
      ...accepted,
      category,
      items: [{ ...accepted.items[0], status: category, decision: null }],
    };
    expect(validRunQueue(data, "o", category)).toBe(true);
    expect(
      validRunQueue(
        { ...data, items: [{ ...data.items[0], decision: "accepted" }] },
        "o",
        category,
      ),
    ).toBe(false);
    expect(
      validRunQueue(
        { ...data, items: [{ ...data.items[0], status: "succeeded" }] },
        "o",
        category,
      ),
    ).toBe(false);
    await runQueueApi.read("o", category);
    expect(request).toHaveBeenCalledWith(
      `/organizations/o/run-queue?category=${category}&limit=20`,
      { cache: "no-store" },
    );
  },
);

it("binds filtered responses and requests to the selected worker", async () => {
  const filtered = { ...accepted, worker_id: "worker / one" };
  expect(
    validRunQueue(filtered, "o", "accepted", undefined, "worker / one"),
  ).toBe(true);
  expect(validRunQueue(filtered, "o", "accepted")).toBe(false);
  expect(
    validRunQueue(accepted, "o", "accepted", undefined, "worker / one"),
  ).toBe(false);
  await runQueueApi.read("o", "accepted", 5, "worker / one");
  expect(request).toHaveBeenCalledWith(
    "/organizations/o/run-queue?category=accepted&limit=20&before=5&worker_id=worker%20%2F%20one",
    { cache: "no-store" },
  );
});
