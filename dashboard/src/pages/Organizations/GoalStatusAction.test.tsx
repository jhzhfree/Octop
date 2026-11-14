import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { expect, it, vi } from "vitest";
import GoalStatusAction from "./GoalStatusAction";
import type { DigitalWorkerGoal } from "../../api/modules/organizations";
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const goal: DigitalWorkerGoal = {
  goal_id: "g1",
  worker_id: "w1",
  title: "Quarterly report",
  description: "",
  success_criteria: "Approved report",
  period_start: null,
  period_end: null,
  status: "active",
  created_by: 1,
  created_at: 1,
  updated_at: 1,
};
const open = () =>
  fireEvent.click(
    screen.getByRole("button", { name: "organizations.completeGoal" }),
  );
const confirm = () =>
  fireEvent.click(
    screen.getByRole("button", { name: /goalLifecycle.confirm/ }),
  );
it("requires explicit confirmation and cancelling never changes the goal", async () => {
  const change = vi.fn().mockResolvedValue(undefined);
  render(<GoalStatusAction goal={goal} onChange={change} />);
  open();
  expect(screen.getByText(goal.title)).toBeTruthy();
  expect(screen.getByText(goal.success_criteria)).toBeTruthy();
  expect(screen.getByText("goalLifecycle.notice")).toBeTruthy();
  expect(change).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "common.cancel" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(change).not.toHaveBeenCalled();
});
it("suppresses duplicate submission and closes after success", async () => {
  let resolve!: () => void;
  const change = vi.fn(
    () =>
      new Promise<void>((r) => {
        resolve = r;
      }),
  );
  render(<GoalStatusAction goal={goal} onChange={change} />);
  open();
  confirm();
  confirm();
  expect(change).toHaveBeenCalledTimes(1);
  await act(async () => resolve());
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});
it("keeps failed confirmation open for deliberate retry", async () => {
  const change = vi
    .fn()
    .mockRejectedValueOnce(new Error("failed"))
    .mockResolvedValue(undefined);
  render(<GoalStatusAction goal={goal} onChange={change} />);
  open();
  confirm();
  await screen.findByText("goalLifecycle.failed");
  expect(screen.getByRole("dialog")).toBeTruthy();
  confirm();
  await waitFor(() => expect(change).toHaveBeenCalledTimes(2));
});
it("activates drafts directly and hides terminal actions", async () => {
  const change = vi.fn().mockResolvedValue(undefined);
  const view = render(
    <GoalStatusAction goal={{ ...goal, status: "draft" }} onChange={change} />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "organizations.activateGoal" }),
  );
  await waitFor(() => expect(change).toHaveBeenCalledTimes(1));
  view.rerender(
    <GoalStatusAction
      goal={{ ...goal, status: "completed" }}
      onChange={change}
    />,
  );
  expect(screen.queryByRole("button")).toBeNull();
});
it("drops confirmation when identity or permissions change", () => {
  const change = vi.fn();
  const view = render(<GoalStatusAction goal={goal} onChange={change} />);
  open();
  view.rerender(
    <GoalStatusAction goal={{ ...goal, goal_id: "g2" }} onChange={change} />,
  );
  expect(screen.queryByRole("dialog")).toBeNull();
  open();
  view.rerender(
    <GoalStatusAction
      goal={{ ...goal, goal_id: "g2" }}
      disabled
      onChange={change}
    />,
  );
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(change).not.toHaveBeenCalled();
});
it("ignores late completion after unmount", async () => {
  let resolve!: () => void;
  const change = vi.fn(
    () =>
      new Promise<void>((r) => {
        resolve = r;
      }),
  );
  const view = render(<GoalStatusAction goal={goal} onChange={change} />);
  open();
  confirm();
  view.unmount();
  await act(async () => resolve());
  expect(screen.queryByRole("dialog")).toBeNull();
});
