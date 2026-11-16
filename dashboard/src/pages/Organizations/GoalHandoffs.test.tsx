import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import GoalHandoffs from "./GoalHandoffs";
import { SensitiveAccessContext } from "../../components/SensitiveAccessBoundary";
import { goalHandoffsApi } from "../../api/modules/goalHandoffs";
import {
  organizationsApi,
  type DigitalWorker,
  type DigitalWorkerGoal,
} from "../../api/modules/organizations";
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ i18n: { language: "en" } }),
}));
vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => ({ id: 1, role: "user" }),
}));
vi.mock("../../api/modules/goalHandoffs", () => ({
  goalHandoffsApi: { create: vi.fn(), list: vi.fn() },
}));
vi.mock("../../api/modules/organizations", () => ({
  organizationsApi: { digitalWorkers: vi.fn() },
}));
const worker = {
  worker_id: "w",
  organization_id: "o",
  position_id: "p",
  status: "active",
  version: 1,
} as DigitalWorker;
const goal = {
  goal_id: "g",
  worker_id: "w",
  title: "Inspect",
  success_criteria: "Evidence",
  status: "active",
  version: 2,
} as DigitalWorkerGoal;
const receiver = {
  ...worker,
  worker_id: "w2",
  position_id: "p2",
  name: "Reviewer",
};
const result = {
  handoff_id: "h",
  organization_id: "o",
  source_goal_id: "g",
  target_goal_id: "g2",
  target_worker_id: "w2",
  created_by: 1,
  reason: "Review evidence",
  source_snapshot: { worker_name: "Ops", title: "Inspect", version: 2 },
  target_snapshot: { name: "Reviewer", position_title: "Review" },
  target_goal_status: "draft",
};
const props = {
  organizationId: "o",
  canManage: true,
  source: { worker, goal },
  onOpenWorker: vi.fn(),
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(goalHandoffsApi.list).mockResolvedValue({
    items: [],
    next_before: null,
  });
  vi.mocked(organizationsApi.digitalWorkers).mockResolvedValue([receiver]);
  vi.mocked(goalHandoffsApi.create).mockResolvedValue(result);
});
async function openAndFill() {
  fireEvent.click(screen.getByRole("button", { name: "Role handoff" }));
  await waitFor(() =>
    expect(organizationsApi.digitalWorkers).toHaveBeenCalled(),
  );
  fireEvent.mouseDown(screen.getByRole("combobox"));
  fireEvent.click(await screen.findByText("Reviewer · p2"));
  fireEvent.change(screen.getByLabelText("Handoff reason"), {
    target: { value: "Review evidence" },
  });
}
it("requires confirmation, creates a draft and opens existing goal workflow", async () => {
  render(<GoalHandoffs {...props} />);
  await openAndFill();
  expect(goalHandoffsApi.create).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole("button", { name: "Confirm receiving draft" }),
  );
  await screen.findByText("Handoff recorded");
  expect(goalHandoffsApi.create).toHaveBeenCalledWith(
    "o",
    "w",
    "g",
    expect.objectContaining({
      expected_source_version: 2,
      expected_target_version: 1,
      target_worker_id: "w2",
    }),
  );
  fireEvent.click(
    screen.getByRole("button", {
      name: "Open receiving worker's goals and plans",
    }),
  );
  expect(props.onOpenWorker).toHaveBeenCalledWith("w2");
});
it("retries an uncertain outcome using the exact same immutable request", async () => {
  vi.mocked(goalHandoffsApi.create).mockRejectedValueOnce(
    new Error("lost response"),
  );
  render(<GoalHandoffs {...props} />);
  await openAndFill();
  fireEvent.click(
    screen.getByRole("button", { name: "Confirm receiving draft" }),
  );
  await screen.findByText(/Read or handoff failed/);
  expect(
    (screen.getByLabelText("Handoff reason") as HTMLInputElement).disabled,
  ).toBe(true);
  fireEvent.click(
    screen.getByRole("button", { name: "Retry original handoff" }),
  );
  await screen.findByText("Handoff recorded");
  const calls = vi.mocked(goalHandoffsApi.create).mock.calls;
  expect(calls[0][3]).toEqual(calls[1][3]);
});
it("hides the action from non-managers", () => {
  render(<GoalHandoffs {...props} canManage={false} />);
  expect(screen.queryByRole("button")).toBeNull();
  expect(goalHandoffsApi.list).not.toHaveBeenCalled();
});
it("clears sensitive content on revocation and ignores late history", async () => {
  let resolve!: (value: {
    items: (typeof result)[];
    next_before: null;
  }) => void;
  vi.mocked(goalHandoffsApi.list).mockReturnValue(
    new Promise((r) => {
      resolve = r;
    }),
  );
  const view = render(
    <SensitiveAccessContext.Provider value={true}>
      <GoalHandoffs {...props} />
    </SensitiveAccessContext.Provider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Role handoff" }));
  view.rerender(
    <SensitiveAccessContext.Provider value={false}>
      <GoalHandoffs {...props} />
    </SensitiveAccessContext.Provider>,
  );
  await act(async () => resolve({ items: [result], next_before: null }));
  expect(screen.queryByText("Review evidence")).toBeNull();
  expect(screen.queryByRole("dialog")).toBeNull();
});
it("rejects another organization's history and clears stale content on error", async () => {
  vi.mocked(goalHandoffsApi.list)
    .mockResolvedValueOnce({ items: [result], next_before: null })
    .mockResolvedValueOnce({
      items: [{ ...result, organization_id: "other" }],
      next_before: null,
    });
  render(<GoalHandoffs {...props} source={undefined} />);
  fireEvent.click(screen.getByRole("button", { name: "Handoff history" }));
  await screen.findByText("Review evidence");
  fireEvent.click(screen.getByRole("button", { name: "Refresh history" }));
  await screen.findByText(/Read or handoff failed/);
  expect(screen.queryByText("Review evidence")).toBeNull();
});
it("shows the current target status on idempotent replay instead of claiming a new draft", async () => {
  vi.mocked(goalHandoffsApi.create).mockResolvedValue({
    ...result,
    target_goal_status: "cancelled",
  });
  render(<GoalHandoffs {...props} />);
  await openAndFill();
  fireEvent.click(
    screen.getByRole("button", { name: "Confirm receiving draft" }),
  );
  await screen.findByText("Handoff recorded");
  expect(screen.getByText(/g2 · cancelled/)).toBeTruthy();
  expect(screen.queryByText(/g2 · draft/)).toBeNull();
});
