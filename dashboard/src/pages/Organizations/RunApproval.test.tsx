import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import RunApproval, { approvalMessages } from "./RunApproval";
import {
  organizationsApi,
  type EnterpriseSceneRun,
} from "../../api/modules/organizations";
import { SensitiveAccessContext } from "../../components/SensitiveAccessBoundary";
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ i18n: { language: "en" } }),
}));
vi.mock("../../api/modules/organizations", () => ({
  organizationsApi: { sceneRun: vi.fn(), decideSceneRun: vi.fn() },
}));
const m = approvalMessages.en;
const row = {
  organization_id: "o",
  run_id: "r",
  requested_by: 1,
  status: "awaiting_approval",
  prompt_snapshot: "PRIVATE REQUEST",
  mcp_servers: [],
  plan_id: "p",
  plan_snapshot: {
    worker: { worker_id: "w", name: "Ops" },
    capabilities: {},
    goal: { title: "Inspect", success_criteria: "Evidence" },
    plan: {
      plan_id: "p",
      title: "Read only",
      version: 1,
      approach: "Inspect status",
      steps: [{ action: "Read", expected_output: "Report" }],
    },
    inspection: {
      mode: "http_inspection",
      share_results: true,
      sources: [
        {
          job_id: "j",
          job_version: 1,
          name: "Status source",
          connection_id: "c",
          connection_version: 1,
        },
      ],
    },
  },
} as EnterpriseSceneRun;
const done = vi.fn();
const props = {
  organizationId: "o",
  runId: "r",
  currentUserId: 2,
  onDecided: done,
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(organizationsApi.sceneRun).mockReset().mockResolvedValue(row);
  vi.mocked(organizationsApi.decideSceneRun)
    .mockReset()
    .mockResolvedValue({ ...row, status: "queued", approved_by: 2 });
});
afterEach(cleanup);
async function open() {
  fireEvent.click(screen.getByText(m.open));
  await screen.findByText("PRIVATE REQUEST");
}
async function choose() {
  await open();
  fireEvent.click(screen.getByText(m.approve));
}
it("reviews the exact run and requires explicit confirmation, including cancel", async () => {
  render(<RunApproval {...props} />);
  expect(organizationsApi.sceneRun).not.toHaveBeenCalled();
  await choose();
  expect(organizationsApi.sceneRun).toHaveBeenCalledWith("o", "r");
  expect(screen.getByText(/Status source/)).toBeTruthy();
  fireEvent.click(screen.getByText(m.cancel));
  expect(organizationsApi.decideSceneRun).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText(m.note), {
    target: { value: " Reviewed " },
  });
  fireEvent.click(screen.getByText(m.approve));
  fireEvent.click(screen.getByText(m.confirm));
  await vi.waitFor(() => expect(done).toHaveBeenCalledTimes(1));
  expect(organizationsApi.decideSceneRun).toHaveBeenCalledWith(
    "o",
    "r",
    "approve",
    "Reviewed",
  );
});
it("allows rejecting a legacy planless request but not approving it", async () => {
  vi.mocked(organizationsApi.sceneRun).mockResolvedValue({
    ...row,
    plan_id: null,
    plan_snapshot: null,
  });
  vi.mocked(organizationsApi.decideSceneRun).mockResolvedValue({
    ...row,
    status: "rejected",
    approved_by: 2,
  });
  render(<RunApproval {...props} />);
  await open();
  expect(screen.getByText(m.legacy)).toBeTruthy();
  expect(screen.getByText(m.approve).closest("button")).toBeDisabled();
  fireEvent.click(screen.getByText(m.reject));
  fireEvent.click(screen.getByText(m.confirm));
  await vi.waitFor(() => expect(done).toHaveBeenCalledTimes(1));
  expect(organizationsApi.decideSceneRun).toHaveBeenCalledWith(
    "o",
    "r",
    "reject",
    "",
  );
});
it.each(["self", "decided"])("blocks %s decisions", async (kind) => {
  vi.mocked(organizationsApi.sceneRun).mockResolvedValue({
    ...row,
    ...(kind === "self"
      ? { requested_by: 2 }
      : { status: "rejected" as const }),
  });
  render(<RunApproval {...props} />);
  await open();
  expect(screen.getByText(kind === "self" ? m.self : m.stale)).toBeTruthy();
  expect(screen.queryByText(m.confirm)).toBeNull();
  expect(screen.queryByText(m.approve)).toBeNull();
});
it.each(["organization_id", "run_id"])(
  "rejects a mismatched %s",
  async (field) => {
    vi.mocked(organizationsApi.sceneRun).mockResolvedValue({
      ...row,
      [field]: "other",
    });
    render(<RunApproval {...props} />);
    fireEvent.click(screen.getByText(m.open));
    await screen.findByText(m.failed);
    expect(screen.queryByText("PRIVATE REQUEST")).toBeNull();
  },
);
it.each([401, 403, 409, 500])(
  "clears the snapshot after a %s and never retries automatically",
  async (status) => {
    vi.mocked(organizationsApi.decideSceneRun).mockRejectedValue({ status });
    render(<RunApproval {...props} />);
    await choose();
    fireEvent.click(screen.getByText(m.confirm));
    await screen.findByText(m.failed);
    expect(screen.queryByText("PRIVATE REQUEST")).toBeNull();
    expect(screen.queryByText(m.confirm)).toBeNull();
    expect(organizationsApi.decideSceneRun).toHaveBeenCalledTimes(1);
    expect(done).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText(m.close));
    await open();
    expect(organizationsApi.sceneRun).toHaveBeenCalledTimes(2);
  },
);
it("serializes double submits and ignores a late decision after close", async () => {
  let resolve!: (r: EnterpriseSceneRun) => void;
  vi.mocked(organizationsApi.decideSceneRun).mockImplementation(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  render(<RunApproval {...props} />);
  await choose();
  fireEvent.click(screen.getByText(m.confirm));
  fireEvent.click(screen.getByText(m.confirm));
  expect(organizationsApi.decideSceneRun).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByText(m.close));
  await act(async () => resolve({ ...row, status: "queued", approved_by: 2 }));
  expect(done).not.toHaveBeenCalled();
  expect(screen.queryByText("PRIVATE REQUEST")).toBeNull();
});
it.each(["run", "organization", "user", "revoked", "unmounted"])(
  "discards late review after %s context change",
  async (kind) => {
    let resolve!: (r: EnterpriseSceneRun) => void;
    vi.mocked(organizationsApi.sceneRun).mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    const view = render(
      <SensitiveAccessContext.Provider value={true}>
        <RunApproval {...props} />
      </SensitiveAccessContext.Provider>,
    );
    fireEvent.click(screen.getByText(m.open));
    if (kind === "unmounted") view.unmount();
    else
      view.rerender(
        <SensitiveAccessContext.Provider value={kind !== "revoked"}>
          <RunApproval
            {...props}
            runId={kind === "run" ? "other" : "r"}
            organizationId={kind === "organization" ? "other" : "o"}
            currentUserId={kind === "user" ? 3 : 2}
          />
        </SensitiveAccessContext.Provider>,
      );
    await act(async () => resolve(row));
    expect(screen.queryByText("PRIVATE REQUEST")).toBeNull();
    expect(organizationsApi.decideSceneRun).not.toHaveBeenCalled();
  },
);
it("rejects an unconfirmed decision response", async () => {
  vi.mocked(organizationsApi.decideSceneRun).mockResolvedValue({
    ...row,
    status: "queued",
    approved_by: 99,
  });
  render(<RunApproval {...props} />);
  await choose();
  fireEvent.click(screen.getByText(m.confirm));
  await screen.findByText(m.failed);
  expect(done).not.toHaveBeenCalled();
});
