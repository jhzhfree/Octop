import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import en from "../../locales/en.json";
import RunOperationTrace from "./RunOperationTrace";
import {
  operationTraceApi,
  validOperationTrace,
  type OperationTrace,
} from "../../api/modules/operationTrace";
vi.mock("../../api/modules/operationTrace", async (original) => ({
  ...(await original<typeof import("../../api/modules/operationTrace")>()),
  operationTraceApi: { read: vi.fn() },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) =>
      key
        .split(".")
        .reduce(
          (value, part) => (value as Record<string, unknown>)[part],
          en as unknown,
        ),
  }),
}));
vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));
const props = {
  organizationId: "o",
  runId: "r",
  currentUserId: 2,
  canRead: true,
};
const record: OperationTrace = {
  organization_id: "o",
  run_id: "r",
  as_of: 100,
  history_complete: false,
  execution_authorized: false,
  truncated: false,
  execution_context: {
    agent_id: "worker",
    on_behalf_of_user_id: 1,
    bound_cron_id: "c",
    configured_executor: "agent_task",
    schedule_kind: "one_shot",
  },
  events: [
    {
      record_id: 1,
      recorded_at: 100,
      action: "scene.run_approved",
      actor_kind: "user_account",
      actor_user_id: 2,
      interaction_mode: "not_recorded",
    },
  ],
};
const read = () => fireEvent.click(screen.getByText("View operation trace"));
beforeEach(() =>
  vi.mocked(operationTraceApi.read).mockReset().mockResolvedValue(record),
);
afterEach(cleanup);
it("shows server account evidence with uncertainty, reads on demand and clears denied reread", async () => {
  render(<RunOperationTrace {...props} />);
  expect(operationTraceApi.read).not.toHaveBeenCalled();
  read();
  await screen.findByText(/User account #2/);
  expect(
    screen.getByText(/Account records do not prove a human click/),
  ).toBeTruthy();
  expect(screen.getByText(/One-shot task/)).toBeTruthy();
  vi.mocked(operationTraceApi.read).mockRejectedValueOnce(new Error("SECRET"));
  read();
  expect(screen.queryByText(/User account #2/)).toBeNull();
  await screen.findByText(/Unable to read trace/);
  expect(screen.queryByText("SECRET")).toBeNull();
});
it.each(["run", "organization", "identity", "permission", "close"])(
  "discards delayed response on %s change",
  async (change) => {
    let resolve!: (data: OperationTrace) => void;
    vi.mocked(operationTraceApi.read).mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    const view = render(<RunOperationTrace {...props} />);
    read();
    read();
    expect(operationTraceApi.read).toHaveBeenCalledTimes(1);
    if (change === "close")
      fireEvent.click(screen.getByText("Close operation trace"));
    else {
      view.rerender(
        <RunOperationTrace
          {...props}
          organizationId={change === "organization" ? "other" : "o"}
          runId={change === "run" ? "other" : "r"}
          currentUserId={change === "identity" ? 3 : 2}
          canRead={change !== "permission"}
        />,
      );
      view.rerender(<RunOperationTrace {...props} />);
    }
    await act(async () => resolve(record));
    expect(screen.queryByText(/User account #2/)).toBeNull();
    read();
    await screen.findByText(/User account #2/);
  },
);
it("rejects mismatched or fabricated response claims", () => {
  expect(validOperationTrace(record, "o", "r")).toBe(true);
  for (const change of [
    { organization_id: "other" },
    { run_id: "other" },
    { history_complete: true },
    { execution_authorized: true },
    { events: [...record.events, ...record.events] },
    {
      events: [
        {
          ...record.events[0],
          actor_kind: "system",
          actor_user_id: null,
          interaction_mode: "service_execution",
        },
      ],
    },
    { events: [{ ...record.events[0], interaction_mode: "human" }] },
  ]) {
    expect(
      validOperationTrace({ ...record, ...change } as OperationTrace, "o", "r"),
    ).toBe(false);
  }
});
it("mismatched run content is never displayed", async () => {
  vi.mocked(operationTraceApi.read).mockResolvedValueOnce({
    ...record,
    run_id: "foreign",
  });
  render(<RunOperationTrace {...props} />);
  read();
  await screen.findByText(/Unable to read trace/);
  expect(screen.queryByText(/User account #2/)).toBeNull();
});
