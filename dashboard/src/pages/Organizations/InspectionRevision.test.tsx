import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import InspectionRevision, { revisionMessages } from "./InspectionRevision";
import { organizationsApi } from "../../api/modules/organizations";
import { deliveryAcceptanceApi } from "../../api/modules/deliveryAcceptance";
import { inspectionRunApi } from "../../api/modules/inspectionRun";
import { inspectionRunMessages } from "./InspectionRunOptions";
import { SensitiveAccessContext } from "../../components/SensitiveAccessBoundary";
vi.mock("../../api/modules/organizations", () => ({
  organizationsApi: {
    sceneRun: vi.fn(),
    digitalWorkers: vi.fn(),
    digitalWorkerGoals: vi.fn(),
    createDigitalWorkerPlan: vi.fn(),
    updateDigitalWorkerPlan: vi.fn(),
    requestSceneRun: vi.fn(),
    decideSceneRun: vi.fn(),
  },
}));
vi.mock("../../api/modules/deliveryAcceptance", async (original) => ({
  ...(await original<typeof import("../../api/modules/deliveryAcceptance")>()),
  deliveryAcceptanceApi: { feedback: vi.fn() },
}));
vi.mock("../../api/modules/inspectionRun", () => ({
  inspectionRunApi: { candidates: vi.fn() },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ i18n: { language: "en" } }),
}));
const m = revisionMessages.en;
const target = { organizationId: "o", runId: "old" };
const source = {
  run_id: "old",
  organization_id: "o",
  scene_id: "scene",
  requested_by: 1,
  status: "succeeded",
  plan_id: "original-plan",
  plan_snapshot: {
    inspection: { mode: "http_inspection" },
    worker: { worker_id: "w" },
    goal: { goal_id: "g" },
    plan: {
      plan_id: "original-plan",
      title: "Original inspection",
      approach: "Original approach",
      steps: [{ action: "Inspect", expected_output: "Report" }],
    },
  },
};
const draft = {
  plan_id: "new-plan",
  goal_id: "g",
  version: 1,
  status: "draft",
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(organizationsApi.sceneRun).mockResolvedValue(source as never);
  vi.mocked(deliveryAcceptanceApi.feedback).mockResolvedValue({
    run_id: "old",
    organization_id: "o",
    decision: "changes_requested",
    reviewer_id: 2,
    reviewed_at: 1,
    review_note: "Add retry evidence",
  } as never);
  vi.mocked(organizationsApi.digitalWorkers).mockResolvedValue([
    { worker_id: "w", organization_id: "o", status: "active" },
  ] as never);
  vi.mocked(organizationsApi.digitalWorkerGoals).mockResolvedValue([
    { goal_id: "g", worker_id: "w", status: "active" },
  ] as never);
  vi.mocked(organizationsApi.createDigitalWorkerPlan).mockResolvedValue(
    draft as never,
  );
  vi.mocked(organizationsApi.updateDigitalWorkerPlan).mockResolvedValue({
    ...draft,
    version: 2,
    status: "ready",
  } as never);
  vi.mocked(organizationsApi.requestSceneRun).mockResolvedValue({
    run_id: "new-run",
    organization_id: "o",
    scene_id: "scene",
    plan_id: "new-plan",
    status: "awaiting_approval",
  } as never);
  vi.mocked(inspectionRunApi.candidates).mockResolvedValue([
    {
      job_id: "job",
      name: "Test source",
      job_version: 1,
      connection_version: 1,
    },
  ]);
});
async function open() {
  fireEvent.click(screen.getByRole("button", { name: m.open }));
  await screen.findByRole("textbox", { name: m.title });
}
it("requires explicit draft, readiness, source selection, sharing and new approval request", async () => {
  render(<InspectionRevision target={target} currentUserId={1} />);
  expect(organizationsApi.sceneRun).not.toHaveBeenCalled();
  await open();
  expect(screen.getByText(/Add retry evidence/)).toBeInTheDocument();
  expect(organizationsApi.createDigitalWorkerPlan).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole("textbox", { name: m.approach }), {
    target: { value: "Collect fresh retry evidence" },
  });
  fireEvent.click(screen.getByRole("button", { name: m.create }));
  await screen.findByRole("button", { name: m.ready });
  expect(organizationsApi.createDigitalWorkerPlan).toHaveBeenCalledWith(
    "o",
    "w",
    "g",
    {
      title: "Original inspection",
      approach: "Collect fresh retry evidence",
      steps: [{ action: "Inspect", expected_output: "Report" }],
    },
  );
  expect(organizationsApi.updateDigitalWorkerPlan).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: m.ready }));
  await screen.findByRole("button", { name: m.request });
  expect(organizationsApi.updateDigitalWorkerPlan).toHaveBeenCalledWith(
    "o",
    "w",
    "g",
    "new-plan",
    1,
    { status: "ready" },
  );
  expect(screen.getByRole("button", { name: m.request })).toBeDisabled();
  fireEvent.click(
    screen.getByRole("checkbox", { name: inspectionRunMessages.en.mode }),
  );
  fireEvent.click(await screen.findByRole("checkbox", { name: /Test source/ }));
  expect(screen.getByRole("button", { name: m.request })).toBeDisabled();
  fireEvent.click(
    screen.getByRole("checkbox", { name: inspectionRunMessages.en.share }),
  );
  fireEvent.click(screen.getByRole("button", { name: m.request }));
  await screen.findByText(`${m.submitted}: new-run`);
  expect(organizationsApi.requestSceneRun).toHaveBeenCalledWith(
    "o",
    "scene",
    "new-plan",
    { useMemory: false, captureMemory: false },
    { jobIds: ["job"], share: true },
  );
  expect(organizationsApi.decideSceneRun).not.toHaveBeenCalled();
});
it("refuses another actor and an inactive goal before writes", async () => {
  const view = render(<InspectionRevision target={target} currentUserId={2} />);
  fireEvent.click(screen.getByRole("button", { name: m.open }));
  await screen.findByText(m.unavailable);
  expect(organizationsApi.createDigitalWorkerPlan).not.toHaveBeenCalled();
  view.unmount();
  vi.mocked(organizationsApi.digitalWorkerGoals).mockResolvedValue([
    { goal_id: "g", worker_id: "w", status: "completed" },
  ] as never);
  render(<InspectionRevision target={target} currentUserId={1} />);
  fireEvent.click(screen.getByRole("button", { name: m.open }));
  await screen.findByText(m.unavailable);
  expect(organizationsApi.createDigitalWorkerPlan).not.toHaveBeenCalled();
});
it("never retries an uncertain draft save or automatically submits", async () => {
  vi.mocked(organizationsApi.createDigitalWorkerPlan).mockRejectedValue(
    new Error("uncertain"),
  );
  render(<InspectionRevision target={target} currentUserId={1} />);
  await open();
  const button = screen.getByRole("button", { name: m.create });
  fireEvent.click(button);
  fireEvent.click(button);
  await screen.findByText(m.unavailable);
  expect(organizationsApi.createDigitalWorkerPlan).toHaveBeenCalledOnce();
  expect(organizationsApi.requestSceneRun).not.toHaveBeenCalled();
});
it("ignores a late draft response after close and clears on access loss", async () => {
  let finish!: (value: never) => void;
  vi.mocked(organizationsApi.createDigitalWorkerPlan).mockReturnValueOnce(
    new Promise((r) => {
      finish = r;
    }),
  );
  const view = render(
    <SensitiveAccessContext.Provider value={true}>
      <InspectionRevision target={target} currentUserId={1} />
    </SensitiveAccessContext.Provider>,
  );
  await open();
  fireEvent.click(screen.getByRole("button", { name: m.create }));
  fireEvent.click(screen.getByRole("button", { name: m.close }));
  await act(async () => finish(draft as never));
  expect(screen.queryByRole("button", { name: m.ready })).toBeNull();
  await open();
  view.rerender(
    <SensitiveAccessContext.Provider value={false}>
      <InspectionRevision target={target} currentUserId={1} />
    </SensitiveAccessContext.Provider>,
  );
  await waitFor(() =>
    expect(screen.queryByText(/Add retry evidence/)).toBeNull(),
  );
});
