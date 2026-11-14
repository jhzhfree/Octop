import {
  cleanup,
  fireEvent,
  render,
  screen,
  act,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import RunQueue from "./RunQueue";
import {
  runQueueApi,
  type RunQueue as Queue,
} from "../../api/modules/runQueue";
vi.mock("../../api/modules/runQueue", async (original) => ({
  ...(await original<typeof import("../../api/modules/runQueue")>()),
  runQueueApi: { read: vi.fn() },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));
const result = (org = "o"): Queue => ({
  organization_id: org,
  category: "unreviewed_success",
  as_of: 100,
  items: [
    { run_id: "r1", status: "succeeded", created_at: 90, decision: null },
  ],
  next_before: 5,
  execution_authorized: false,
  persisted_state_may_lag: true,
});
beforeEach(() => {
  vi.mocked(runQueueApi.read).mockReset().mockResolvedValue(result());
});
afterEach(cleanup);
const load = () =>
  fireEvent.click(screen.getByRole("button", { name: /runQueue.refresh/ }));
it("does not read without management permission", () => {
  render(<RunQueue organizationId="o" canManage={false} />);
  expect(runQueueApi.read).not.toHaveBeenCalled();
});
it("shows records and pages without appending stale data", async () => {
  render(<RunQueue organizationId="o" canManage />);
  load();
  expect(await screen.findByText("r1")).toBeTruthy();
  vi.mocked(runQueueApi.read).mockResolvedValueOnce({
    ...result(),
    items: [],
    next_before: null,
  });
  fireEvent.click(screen.getByRole("button", { name: "runQueue.next" }));
  await screen.findByText("runQueue.empty");
  expect(runQueueApi.read).toHaveBeenLastCalledWith(
    "o",
    "unreviewed_success",
    5,
  );
  expect(screen.queryByText("r1")).toBeNull();
});
it("rejects mismatched organization", async () => {
  vi.mocked(runQueueApi.read).mockResolvedValue(result("other"));
  render(<RunQueue organizationId="o" canManage />);
  load();
  expect(await screen.findByText("runQueue.error")).toBeTruthy();
  expect(screen.queryByText("r1")).toBeNull();
});
it("ignores in-flight results after category changes and suppresses duplicate clicks", async () => {
  let resolve!: (q: Queue) => void;
  vi.mocked(runQueueApi.read).mockImplementationOnce(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  render(<RunQueue organizationId="o" canManage />);
  load();
  load();
  expect(runQueueApi.read).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByRole("combobox"), {
    target: { value: "failed" },
  });
  await act(async () => resolve(result()));
  expect(screen.queryByText("r1")).toBeNull();
});
it("ignores in-flight results after organization changes", async () => {
  let resolve!: (q: Queue) => void;
  vi.mocked(runQueueApi.read).mockImplementationOnce(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  const view = render(<RunQueue organizationId="o" canManage />);
  load();
  view.rerender(<RunQueue organizationId="other" canManage />);
  await act(async () => resolve(result()));
  expect(screen.queryByText("r1")).toBeNull();
});
