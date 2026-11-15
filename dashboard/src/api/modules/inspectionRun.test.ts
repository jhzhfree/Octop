import { beforeEach, expect, it, vi } from "vitest";
import { request } from "../request";
import { inspectionRunApi } from "./inspectionRun";
import { organizationsApi } from "./organizations";
vi.mock("../request", () => ({ request: vi.fn() }));
beforeEach(() => vi.resetAllMocks());
it("reads minimal candidates without caching and encodes the scope", () => {
  inspectionRunApi.candidates("org/a", "scene/b");
  expect(request).toHaveBeenCalledWith(
    "/organizations/org%2Fa/scenes/scene%2Fb/inspection-jobs",
    { cache: "no-store" },
  );
});
it("sends explicit selected IDs and sharing without altering ordinary requests", () => {
  organizationsApi.requestSceneRun(
    "org",
    "scene",
    "plan",
    { useMemory: false, captureMemory: false },
    { jobIds: ["job"], share: true },
  );
  expect(request).toHaveBeenLastCalledWith(
    "/organizations/org/scenes/scene/runs",
    {
      method: "POST",
      body: JSON.stringify({
        plan_id: "plan",
        use_memory: false,
        capture_memory: false,
        inspection_job_ids: ["job"],
        share_inspection_results: true,
      }),
    },
  );
  organizationsApi.requestSceneRun("org", "scene", "plan");
  expect(request).toHaveBeenLastCalledWith(
    "/organizations/org/scenes/scene/runs",
    {
      method: "POST",
      body: JSON.stringify({
        plan_id: "plan",
        use_memory: true,
        capture_memory: true,
      }),
    },
  );
});
