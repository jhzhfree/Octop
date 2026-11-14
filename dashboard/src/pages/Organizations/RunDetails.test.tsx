import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import RunDetails from "./RunDetails";
import { runQueueApi, type RunQueueDetail } from "../../api/modules/runQueue";
vi.mock("../../api/modules/runQueue", () => ({
  runQueueApi: { detail: vi.fn() },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));
vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));
const row = {
  run_id: "r",
  organization_id: "o",
  scene_name: "Review",
  scene_version: 1,
  status: "succeeded",
  created_at: 1,
  prompt_snapshot: "PRIVATE PROMPT",
  error_summary: "PRIVATE ERROR",
} as RunQueueDetail;
beforeEach(() =>
  vi.mocked(runQueueApi.detail).mockReset().mockResolvedValue(row),
);
afterEach(cleanup);
it("fetches the exact run and displays metadata without raw content", async () => {
  render(<RunDetails organizationId="o" runId="r" onClose={() => {}} />);
  expect(await screen.findByText("Review (v1)")).toBeTruthy();
  expect(runQueueApi.detail).toHaveBeenCalledWith("o", "r");
  expect(screen.queryByText("PRIVATE PROMPT")).toBeNull();
  expect(screen.queryByText("PRIVATE ERROR")).toBeNull();
});
it("rejects wrong organization or run response", async () => {
  vi.mocked(runQueueApi.detail).mockResolvedValue({
    ...row,
    organization_id: "other",
  });
  render(<RunDetails organizationId="o" runId="r" onClose={() => {}} />);
  expect(await screen.findByText("runDetails.error")).toBeTruthy();
  expect(screen.queryByText("Review (v1)")).toBeNull();
});
it("ignores a closed request after a new detail opens", async () => {
  let resolve!: (v: RunQueueDetail) => void;
  vi.mocked(runQueueApi.detail).mockImplementationOnce(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  const old = render(
    <RunDetails organizationId="o" runId="r" onClose={() => {}} />,
  );
  old.unmount();
  vi.mocked(runQueueApi.detail).mockResolvedValue({
    ...row,
    run_id: "next",
    scene_name: "Current",
  });
  render(<RunDetails organizationId="o" runId="next" onClose={() => {}} />);
  await screen.findByText("Current (v1)");
  await act(async () => resolve(row));
  expect(screen.queryByText("Review (v1)")).toBeNull();
});
