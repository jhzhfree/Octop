import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import RunQueue from "./RunQueue";
import { runQueueApi } from "../../api/modules/runQueue";
import { organizationsApi } from "../../api/modules/organizations";
import { deliveryAcceptanceApi } from "../../api/modules/deliveryAcceptance";
import { mattermostPublicationApi } from "../../api/modules/mattermostPublication";
const identity = vi.hoisted(() => ({
  id: 2,
  role: "admin",
  permissions: ["tasks"],
}));
vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => identity,
}));
vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: "en" } }),
}));
vi.mock("../../api/modules/runQueue", async (original) => ({
  ...(await original<typeof import("../../api/modules/runQueue")>()),
  runQueueApi: { read: vi.fn(), detail: vi.fn() },
}));
vi.mock("../../api/modules/organizations", () => ({
  organizationsApi: { sceneRunEvidence: vi.fn() },
}));
vi.mock("../../api/modules/deliveryAcceptance", async (original) => ({
  ...(await original<typeof import("../../api/modules/deliveryAcceptance")>()),
  deliveryAcceptanceApi: { review: vi.fn(), decide: vi.fn() },
}));
vi.mock("../../api/modules/mattermostPublication", async (original) => ({
  ...(await original<
    typeof import("../../api/modules/mattermostPublication")
  >()),
  mattermostPublicationApi: {
    get: vi.fn(),
    create: vi.fn(),
    candidate: vi.fn(),
    decide: vi.fn(),
  },
}));
beforeEach(() => {
  vi.clearAllMocks();
  identity.id = 2;
  identity.role = "admin";
  identity.permissions = ["tasks"];
  vi.mocked(runQueueApi.read).mockResolvedValue({
    organization_id: "o",
    category: "unreviewed_success",
    as_of: 1,
    items: [
      { run_id: "r", status: "succeeded", created_at: 1, decision: null },
    ],
    next_before: null,
    execution_authorized: false,
    persisted_state_may_lag: true,
  });
  vi.mocked(runQueueApi.detail).mockResolvedValue({
    organization_id: "o",
    run_id: "r",
    scene_id: "s",
    scene_name: "Inspection",
    scene_version: 1,
    plan_id: null,
    status: "succeeded",
    created_at: 1,
    updated_at: 1,
    decision: null,
    as_of: 1,
    persisted_state_may_lag: true,
  });
  vi.mocked(organizationsApi.sceneRunEvidence).mockResolvedValue({
    run_id: "r",
    available: true,
    answer: "SAME RUN REPORT",
  });
  vi.mocked(deliveryAcceptanceApi.review).mockResolvedValue({
    run_id: "r",
    answer: "REVIEW REPORT",
    evidence_sha256: "a".repeat(64),
    snapshot_sha256: "b".repeat(64),
    evidence_length: 13,
    acceptance: null,
  });
  vi.mocked(mattermostPublicationApi.get).mockRejectedValue(
    new Error("missing preview"),
  );
});
afterEach(cleanup);
async function open() {
  fireEvent.click(screen.getByText("runQueue.refresh"));
  fireEvent.click(await screen.findByText("r"));
  await screen.findByText("Inspection (v1)");
}
it("opens the exact run without fetching content, and reads each surface only on click", async () => {
  render(<RunQueue organizationId="o" canManage />);
  await open();
  expect(screen.getByText("operationTrace.view")).toBeTruthy();
  expect(organizationsApi.sceneRunEvidence).not.toHaveBeenCalled();
  expect(deliveryAcceptanceApi.review).not.toHaveBeenCalled();
  expect(mattermostPublicationApi.get).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("organizations.viewEvidence"));
  await screen.findByText("SAME RUN REPORT");
  expect(organizationsApi.sceneRunEvidence).toHaveBeenCalledWith("o", "r");
  fireEvent.click(
    screen.getByRole("button", { name: "Review delivery summary" }),
  );
  await screen.findByText("REVIEW REPORT");
  expect(deliveryAcceptanceApi.review).toHaveBeenCalledWith({
    organizationId: "o",
    runId: "r",
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Mattermost publication review" }),
  );
  expect(mattermostPublicationApi.get).toHaveBeenCalledWith({
    organizationId: "o",
    runId: "r",
  });
  expect(screen.queryByText("Prepare local publication preview")).toBeNull();
  expect(deliveryAcceptanceApi.decide).not.toHaveBeenCalled();
  expect(mattermostPublicationApi.create).not.toHaveBeenCalled();
  expect(mattermostPublicationApi.decide).not.toHaveBeenCalled();
});
it.each(["organization", "permission", "identity", "refresh"])(
  "clears content and discards delayed reads on %s change",
  async (change) => {
    let resolve!: (value: {
      run_id: string;
      available: boolean;
      answer: string;
    }) => void;
    vi.mocked(organizationsApi.sceneRunEvidence).mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    const view = render(<RunQueue organizationId="o" canManage />);
    await open();
    fireEvent.click(screen.getByText("organizations.viewEvidence"));
    if (change === "identity") identity.id = 3;
    if (change === "refresh")
      fireEvent.click(screen.getByText("runQueue.refresh"));
    else
      view.rerender(
        <RunQueue
          organizationId={change === "organization" ? "other" : "o"}
          canManage={change !== "permission"}
        />,
      );
    await act(async () =>
      resolve({ run_id: "r", available: true, answer: "STALE PRIVATE REPORT" }),
    );
    expect(screen.queryByText("STALE PRIVATE REPORT")).toBeNull();
    expect(screen.queryByText("Inspection (v1)")).toBeNull();
  },
);
it("rejects mismatched evidence and clears a report on explicit close", async () => {
  render(<RunQueue organizationId="o" canManage />);
  await open();
  fireEvent.click(screen.getByText("organizations.viewEvidence"));
  await screen.findByText("SAME RUN REPORT");
  fireEvent.click(screen.getByText("runDetails.closeEvidence"));
  expect(screen.queryByText("SAME RUN REPORT")).toBeNull();
  vi.mocked(organizationsApi.sceneRunEvidence).mockResolvedValue({
    run_id: "other",
    available: true,
    answer: "WRONG REPORT",
  });
  fireEvent.click(screen.getByText("organizations.viewEvidence"));
  await screen.findByText("organizations.evidenceLoadFailed");
  expect(screen.queryByText("WRONG REPORT")).toBeNull();
});

it("removes displayed evidence on a denied reread and on observed permission revocation", async () => {
  const view = render(<RunQueue organizationId="o" canManage />);
  await open();
  fireEvent.click(screen.getByText("organizations.viewEvidence"));
  await screen.findByText("SAME RUN REPORT");
  vi.mocked(organizationsApi.sceneRunEvidence).mockRejectedValueOnce(
    new Error("forbidden"),
  );
  fireEvent.click(screen.getByText("organizations.viewEvidence"));
  await screen.findByText("organizations.evidenceLoadFailed");
  expect(screen.queryByText("SAME RUN REPORT")).toBeNull();
  fireEvent.click(screen.getByText("organizations.viewEvidence"));
  await screen.findByText("SAME RUN REPORT");
  identity.role = "member";
  identity.permissions = [];
  view.rerender(<RunQueue organizationId="o" canManage />);
  expect(screen.queryByText("SAME RUN REPORT")).toBeNull();
  await open();
  expect(screen.queryByText("organizations.viewEvidence")).toBeNull();
});

it("lets a human revisit the accepted run and its blocked publication without sending", async () => {
  vi.mocked(deliveryAcceptanceApi.decide).mockResolvedValue({
    run_id: "r",
    organization_id: "o",
    decision: "accepted",
    reviewer_id: 2,
    reviewed_at: 2,
    evidence_sha256: "a".repeat(64),
    snapshot_sha256: "b".repeat(64),
    evidence_length: 13,
    scene_version: 1,
  });
  render(<RunQueue organizationId="o" canManage />);
  await open();
  fireEvent.click(
    screen.getByRole("button", { name: "Review delivery summary" }),
  );
  await screen.findByText("REVIEW REPORT");
  fireEvent.click(screen.getByRole("button", { name: "Accept summary" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Confirm permanent decision" }),
  );
  await screen.findByText("Accepted by a human");
  vi.mocked(runQueueApi.read).mockResolvedValue({
    organization_id: "o",
    category: "accepted",
    as_of: 3,
    items: [
      { run_id: "r", status: "succeeded", created_at: 1, decision: "accepted" },
    ],
    next_before: null,
    execution_authorized: false,
    persisted_state_may_lag: true,
  });
  fireEvent.change(screen.getByRole("combobox"), {
    target: { value: "accepted" },
  });
  expect(screen.queryByText("REVIEW REPORT")).toBeNull();
  await open();
  expect(runQueueApi.read).toHaveBeenLastCalledWith("o", "accepted", undefined);
  expect(runQueueApi.detail).toHaveBeenLastCalledWith("o", "r");
  expect(screen.getByText("runQueue.actions.accepted")).toBeTruthy();
  fireEvent.click(screen.getByText("organizations.viewEvidence"));
  await screen.findByText("SAME RUN REPORT");
  vi.mocked(mattermostPublicationApi.get).mockResolvedValue({
    organization_id: "o",
    run_id: "r",
    worker_id: "worker",
    worker_version: 1,
    intent_revision: 1,
    configuration_revision: 1,
    instance_key: "fixture",
    team_id: "t".repeat(26),
    channel_id: "c".repeat(26),
    bot_id: "b".repeat(26),
    report_sha256: "a".repeat(64),
    publication_sha256: "b".repeat(64),
    content: "EXISTING LOCAL PREVIEW",
    proposed_by: 1,
    approved_by: 2,
    status: "blocked_not_connected",
    created_at: 2,
    updated_at: 3,
    connected: false,
    dispatch_enabled: false,
    execution_authorized: false,
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Mattermost publication review" }),
  );
  await screen.findByText("EXISTING LOCAL PREVIEW");
  expect(
    screen.getByText("Approved locally · blocked: not connected · not sent"),
  ).toBeTruthy();
  expect(mattermostPublicationApi.get).toHaveBeenCalledWith({
    organizationId: "o",
    runId: "r",
  });
  expect(deliveryAcceptanceApi.decide).toHaveBeenCalledTimes(1);
  expect(mattermostPublicationApi.candidate).not.toHaveBeenCalled();
  expect(mattermostPublicationApi.create).not.toHaveBeenCalled();
  expect(mattermostPublicationApi.decide).not.toHaveBeenCalled();
});
