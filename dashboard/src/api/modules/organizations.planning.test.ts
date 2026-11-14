import { expect, it, vi } from "vitest";
import { request } from "../request";
import { organizationsApi } from "./organizations";
vi.mock("../request", () => ({ request: vi.fn() }));
it("encodes planning path and forwards bounded request and cancellation signal", async () => {
  const body = {
    worker_version: 2,
    messages: [{ role: "user" as const, content: "Plan" }],
  };
  const signal = new AbortController().signal;
  await organizationsApi.planningDialogue(
    "org/a",
    "worker/b",
    "goal/c",
    body,
    signal,
  );
  expect(request).toHaveBeenCalledWith(
    "/organizations/org%2Fa/digital-workers/worker%2Fb/goals/goal%2Fc/planning-dialogue",
    { method: "POST", body: JSON.stringify(body), signal },
  );
});
