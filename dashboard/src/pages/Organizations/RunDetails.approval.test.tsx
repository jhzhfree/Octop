import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import RunDetails from "./RunDetails";
import { approvalMessages } from "./RunApproval";
import {
  organizationsApi,
  type EnterpriseSceneRun,
} from "../../api/modules/organizations";
import { runQueueApi, type RunQueueDetail } from "../../api/modules/runQueue";
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: "en" } }),
}));
vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));
vi.mock("../../api/modules/organizations", () => ({
  organizationsApi: { sceneRun: vi.fn(), decideSceneRun: vi.fn() },
}));
vi.mock("../../api/modules/runQueue", () => ({
  runQueueApi: { detail: vi.fn() },
}));
vi.mock("./RunOperationTrace", () => ({
  default: () => <span>operation trace entry</span>,
}));
vi.mock("./RunDelivery", () => ({
  default: () => <span>same-run delivery entry</span>,
}));
const m = approvalMessages.en;
const detail = {
  organization_id: "o",
  run_id: "r",
  scene_id: "s",
  scene_name: "Inspection",
  scene_version: 1,
  status: "awaiting_approval",
  created_at: 1,
  plan_id: "p",
} as RunQueueDetail;
const request = {
  ...detail,
  requested_by: 1,
  approved_by: null,
  prompt_snapshot: "REQUEST SNAPSHOT",
  mcp_servers: [],
  plan_snapshot: {
    worker: { worker_id: "w", name: "Ops" },
    capabilities: {},
    goal: { title: "Goal" },
    plan: { plan_id: "p", title: "Plan", steps: [] },
  },
} as unknown as EnterpriseSceneRun;
const changed = vi.fn();
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(runQueueApi.detail).mockReset().mockResolvedValue(detail);
  vi.mocked(organizationsApi.sceneRun).mockReset().mockResolvedValue(request);
  vi.mocked(organizationsApi.decideSceneRun)
    .mockReset()
    .mockResolvedValue({ ...request, status: "queued", approved_by: 2 });
});
afterEach(cleanup);
function mount() {
  return render(
    <RunDetails
      organizationId="o"
      runId="r"
      currentUserId={2}
      onClose={() => {}}
      onChanged={changed}
    />,
  );
}
async function review() {
  fireEvent.click(await screen.findByText(m.open));
  await screen.findByText("REQUEST SNAPSHOT");
}
it("connects confirmed approval to queued status then explicit refresh and delivery", async () => {
  mount();
  await review();
  vi.mocked(runQueueApi.detail).mockResolvedValue({
    ...detail,
    status: "queued",
  });
  vi.mocked(organizationsApi.sceneRun).mockResolvedValue({
    ...request,
    status: "queued",
  });
  fireEvent.click(screen.getByText(m.approve));
  fireEvent.click(screen.getByText(m.confirm));
  await screen.findByText("organizations.runStatuses.queued");
  expect(changed).toHaveBeenCalledTimes(1);
  expect(screen.queryByText("REQUEST SNAPSHOT")).toBeNull();
  expect(organizationsApi.decideSceneRun).toHaveBeenCalledTimes(1);
  vi.mocked(organizationsApi.sceneRun).mockResolvedValue({
    ...request,
    status: "succeeded",
  });
  vi.mocked(runQueueApi.detail).mockResolvedValue({
    ...detail,
    status: "succeeded",
  });
  fireEvent.click(screen.getByText("runDetails.refresh"));
  await screen.findByText("same-run delivery entry");
  expect(organizationsApi.sceneRun).toHaveBeenLastCalledWith("o", "r");
  expect(organizationsApi.decideSceneRun).toHaveBeenCalledTimes(1);
});
it("clears an open request if refresh is unauthorized and allows explicit reread", async () => {
  mount();
  await review();
  vi.mocked(organizationsApi.sceneRun).mockRejectedValue({ status: 403 });
  fireEvent.click(screen.getByText("runDetails.refresh"));
  await screen.findByText("runDetails.error");
  expect(screen.queryByText("REQUEST SNAPSHOT")).toBeNull();
  expect(screen.queryByText(m.approve)).toBeNull();
  vi.mocked(organizationsApi.sceneRun).mockResolvedValue({
    ...request,
    status: "rejected",
  });
  vi.mocked(runQueueApi.detail).mockResolvedValue({
    ...detail,
    status: "rejected",
  });
  fireEvent.click(screen.getByText("runDetails.refresh"));
  await screen.findByText("organizations.runStatuses.rejected");
  expect(organizationsApi.decideSceneRun).not.toHaveBeenCalled();
});
it("ignores a late refresh after navigating away", async () => {
  const view = mount();
  await screen.findByText("Inspection (v1)");
  let resolve!: (r: EnterpriseSceneRun) => void;
  vi.mocked(organizationsApi.sceneRun).mockImplementationOnce(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  fireEvent.click(screen.getByText("runDetails.refresh"));
  view.unmount();
  await act(async () => resolve({ ...request, status: "succeeded" }));
  expect(runQueueApi.detail).toHaveBeenCalledTimes(1);
  expect(changed).not.toHaveBeenCalled();
});

it("offers failure follow-up without pretending a failed run has a report", async () => {
  vi.mocked(runQueueApi.detail).mockResolvedValue({
    ...detail,
    status: "failed",
  });
  mount();
  await screen.findByRole("button", {
    name: "Request a new HTTP inspection after failure",
  });
  expect(screen.queryByText("same-run delivery entry")).toBeNull();
  expect(organizationsApi.sceneRun).not.toHaveBeenCalled();
});
