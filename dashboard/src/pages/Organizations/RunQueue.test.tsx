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

it("filters by the selected worker and discards previous in-flight results", async () => {
  let resolve!: (value: Queue) => void;
  vi.mocked(runQueueApi.read).mockImplementationOnce(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  render(
    <RunQueue
      organizationId="o"
      canManage
      workers={[
        { worker_id: "w", organization_id: "o", name: "Operations" },
        {
          worker_id: "foreign",
          organization_id: "other",
          name: "Other organization",
        },
      ]}
    />,
  );
  expect(screen.queryByText(/Other organization/)).toBeNull();
  load();
  fireEvent.change(screen.getByRole("combobox", { name: "runQueue.worker" }), {
    target: { value: "w" },
  });
  vi.mocked(runQueueApi.read).mockResolvedValueOnce({
    ...result(),
    worker_id: "w",
    items: [{ ...result().items[0], run_id: "assigned-run" }],
  });
  load();
  await screen.findByText("assigned-run");
  expect(runQueueApi.read).toHaveBeenLastCalledWith(
    "o",
    "unreviewed_success",
    undefined,
    "w",
  );
  await act(async () => resolve(result()));
  expect(screen.queryByText("r1")).toBeNull();
  expect(screen.getByText("assigned-run")).toBeTruthy();
});

it("rejects a server response that ignores the worker filter", async () => {
  render(
    <RunQueue
      organizationId="o"
      canManage
      workers={[{ worker_id: "w", organization_id: "o", name: "Operations" }]}
    />,
  );
  fireEvent.change(screen.getByRole("combobox", { name: "runQueue.worker" }), {
    target: { value: "w" },
  });
  load();
  await screen.findByText("runQueue.error");
  expect(screen.queryByText("r1")).toBeNull();
  vi.mocked(runQueueApi.read).mockResolvedValueOnce({
    ...result(),
    worker_id: "w",
  });
  load();
  await screen.findByText("r1");
  expect(screen.queryByText("runQueue.error")).toBeNull();
});
