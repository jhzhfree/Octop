import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import en from "../../locales/en.json";
import { organizationsApi } from "../../api/modules/organizations";
import { externalKnowledgeApi } from "../../api/modules/externalKnowledge";
import { workerKnowledgeMappingsApi } from "../../api/modules/workerKnowledgeMappings";
import OrganizationsPage from "./index";
import { inspectServerReadiness } from "../../api/modules/planReadiness";
import { serverReadinessMessages } from "./serverReadinessMessages";

vi.mock("react-i18next", async () => {
  const { createInstance } = await import("i18next");
  const { default: messages } = await import("../../locales/en.json");
  const instance = createInstance();
  await instance.init({
    lng: "en",
    resources: { en: { translation: messages } },
    interpolation: { escapeValue: false },
  });
  const t = instance.t.bind(instance);
  return { useTranslation: () => ({ t, i18n: instance }) };
});
vi.mock("../../api/modules/planReadiness", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/modules/planReadiness")>()),
  inspectServerReadiness: vi.fn(),
}));
vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));
vi.mock("../../hooks/useCurrentUser", () => ({ useCurrentUser: () => null }));
vi.mock("../../api/modules/organizations", () => ({
  organizationsApi: {
    list: vi.fn(),
    chart: vi.fn(),
    scenes: vi.fn(),
    sceneRuns: vi.fn(),
    digitalWorkers: vi.fn(),
    digitalWorkerMemoryCandidates: vi.fn(),
    digitalWorkerGoals: vi.fn(),
    digitalWorkerCapabilityProposals: vi.fn(),
    digitalWorkerAuthorizedResources: vi.fn(),
    digitalWorkerPlans: vi.fn(),
    createDigitalWorkerPlan: vi.fn(),
    planningDialogue: vi.fn(),
  },
}));

vi.mock("../../api/modules/externalKnowledge", () => ({
  externalKnowledgeApi: { list: vi.fn() },
}));
vi.mock("../../api/modules/workerKnowledgeMappings", () => ({
  workerKnowledgeMappingsApi: {
    list: vi.fn(),
    create: vi.fn(),
    revoke: vi.fn(),
    inspect: vi.fn(),
  },
}));

const organization = {
  organization_id: "org1",
  owner_user_id: 1,
  name: "Example",
  description: "",
  member_role: "manager" as const,
  created_at: 0,
  updated_at: 0,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(externalKnowledgeApi.list).mockResolvedValue([]);
  vi.mocked(workerKnowledgeMappingsApi.list).mockResolvedValue({
    items: [],
    offset: 0,
    limit: 10,
  });
  vi.mocked(organizationsApi.list).mockResolvedValue([organization]);
  vi.mocked(organizationsApi.chart).mockResolvedValue({
    organization,
    positions: [],
    departments: [],
    members: [],
  });
  vi.mocked(organizationsApi.scenes).mockResolvedValue([]);
  vi.mocked(organizationsApi.sceneRuns).mockResolvedValue([]);
  vi.mocked(organizationsApi.digitalWorkers).mockResolvedValue([
    {
      worker_id: "worker1",
      organization_id: "org1",
      position_id: "position1",
      agent_id: "agent1",
      owner_user_id: 1,
      name: "Research twin",
      position_title: "Researcher",
      agent_name: "Research agent",
      responsibilities: "Research",
      status: "active",
      version: 1,
      capability_version: 1,
      capabilities: {
        skills: [],
        knowledge: [],
        operating_guidance: [],
        skill_package_ids: [],
        knowledge_base_ids: [],
      },
      created_at: 0,
      updated_at: 0,
    },
  ]);
  vi.mocked(organizationsApi.digitalWorkerGoals).mockResolvedValue([
    {
      goal_id: "goal1",
      worker_id: "worker1",
      title: "Weekly market review",
      description: "",
      success_criteria: "Three cited sources",
      status: "active",
      period_start: null,
      period_end: null,
      created_by: 1,
      created_at: 0,
      updated_at: 0,
    },
  ]);
  vi.mocked(organizationsApi.digitalWorkerMemoryCandidates).mockResolvedValue(
    [],
  );
  vi.mocked(
    organizationsApi.digitalWorkerCapabilityProposals,
  ).mockResolvedValue([]);
  vi.mocked(
    organizationsApi.digitalWorkerAuthorizedResources,
  ).mockResolvedValue({ skills: [], knowledge_bases: [] });
  vi.mocked(organizationsApi.digitalWorkerPlans).mockResolvedValue([]);
});

async function openPlans() {
  const user = userEvent.setup();
  render(<OrganizationsPage />);
  await user.click(
    await screen.findByRole("tab", { name: en.organizations.virtualWorkers }),
  );
  await user.click(
    await screen.findByRole("button", { name: en.organizations.manageGoals }),
  );
  await user.click(
    await screen.findByRole("button", { name: en.organizations.managePlans }),
  );
  return user;
}

describe("organization guided planning integration", { timeout: 15000 }, () => {
  it("fills the existing editor and saves only on its explicit confirmation", async () => {
    const user = await openPlans();
    await user.click(
      screen.getByRole("button", { name: en.digitalTwinPlanning.open }),
    );
    await user.type(
      screen.getByLabelText(en.digitalTwinPlanning.sources),
      "Approved connector",
    );
    await user.type(
      screen.getByLabelText(en.digitalTwinPlanning.deliverable),
      "Evidence report",
    );
    await user.type(
      screen.getByLabelText(en.digitalTwinPlanning.reviewer),
      "Research lead",
    );
    await user.click(
      screen.getByRole("button", { name: en.digitalTwinPlanning.continue }),
    );
    // rc-dialog uses a fixed title id in tests; select the sole visible dialog.
    const editor = await screen.findByRole("dialog");
    expect(
      within(editor).getByText(en.organizations.addPlan),
    ).toBeInTheDocument();
    expect(
      within(editor).getByLabelText(en.organizations.planTitle),
    ).toHaveValue("Weekly market review");
    expect(
      (
        within(editor).getByLabelText(
          en.organizations.planApproach,
        ) as HTMLTextAreaElement
      ).value,
    ).toContain("Approved connector");
    expect(organizationsApi.createDigitalWorkerPlan).not.toHaveBeenCalled();
    await user.click(within(editor).getByRole("button", { name: "OK" }));
    await waitFor(() =>
      expect(organizationsApi.createDigitalWorkerPlan).toHaveBeenCalledTimes(1),
    );
    const [org, worker, goal, payload] = vi.mocked(
      organizationsApi.createDigitalWorkerPlan,
    ).mock.calls[0];
    expect([org, worker, goal]).toEqual(["org1", "worker1", "goal1"]);
    expect(payload.steps).toHaveLength(5);
    expect(payload.steps[3].expected_output).toContain("Evidence report");
    expect(payload).not.toHaveProperty("status");
  }, 15000);

  it("requires an explicit model send and confirmation before opening its draft in the editor", async () => {
    const draft = {
      title: "Model draft",
      approach: "Verify the configured sources",
      steps: [{ action: "Collect evidence", expected_output: "Cited report" }],
    };
    vi.mocked(organizationsApi.planningDialogue).mockResolvedValue({
      reply: "Review this proposal",
      model_ref: "gateway/model",
      draft,
    });
    const user = await openPlans();
    await user.click(
      screen.getByRole("button", { name: en.planningDialogue.open }),
    );
    expect(
      screen.getByText(en.planningDialogue.disclosure),
    ).toBeInTheDocument();
    expect(organizationsApi.planningDialogue).not.toHaveBeenCalled();
    await user.type(
      screen.getByLabelText(en.planningDialogue.input),
      "Plan my report",
    );
    await user.click(
      screen.getByRole("button", { name: en.planningDialogue.send }),
    );
    await screen.findByText("Model draft");
    expect(organizationsApi.planningDialogue).toHaveBeenCalledWith(
      "org1",
      "worker1",
      "goal1",
      {
        worker_version: 1,
        messages: [{ role: "user", content: "Plan my report" }],
      },
      expect.any(AbortSignal),
    );
    expect(organizationsApi.createDigitalWorkerPlan).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: en.planningDialogue.useDraft }),
    );
    const editor = await screen.findByRole("dialog");
    expect(
      within(editor).getByLabelText(en.organizations.planTitle),
    ).toHaveValue("Model draft");
    expect(organizationsApi.createDigitalWorkerPlan).not.toHaveBeenCalled();
    await user.click(within(editor).getByRole("button", { name: "Cancel" }));
    expect(organizationsApi.createDigitalWorkerPlan).not.toHaveBeenCalled();
  });

  it("shows read-only preparation and run history for an expanded plan", async () => {
    vi.mocked(organizationsApi.digitalWorkerPlans).mockResolvedValue([
      {
        plan_id: "plan1",
        goal_id: "goal1",
        title: "Saved plan",
        approach: "Read approved sources",
        steps: [{ action: "Collect", expected_output: "Cited evidence" }],
        status: "draft",
        version: 1,
        created_by: 1,
        created_at: 0,
        updated_at: 0,
      },
    ]);
    const user = await openPlans();
    const row = (await screen.findByText("Saved plan")).closest("tr");
    expect(row).not.toBeNull();
    await user.click(within(row!).getByRole("button", { name: "Expand row" }));
    const readiness = screen.getByRole("region", {
      name: en.planReadiness.title,
    });
    expect(
      within(readiness).getByText(en.planReadiness.boundary),
    ).toBeInTheDocument();
    expect(
      within(readiness).getByText(en.planReadiness.noRuns),
    ).toBeInTheDocument();
    expect(organizationsApi.createDigitalWorkerPlan).not.toHaveBeenCalled();
    expect(inspectServerReadiness).not.toHaveBeenCalled();
    const serverPanel = screen.getByRole("region", {
      name: serverReadinessMessages.en.title,
    });
    vi.mocked(inspectServerReadiness).mockResolvedValueOnce({
      organization_id: "org1",
      worker_id: "worker1",
      goal_id: "goal1",
      plan_id: "plan1",
      worker_version: 1,
      capability_version: 1,
      plan_version: 1,
      goal_updated_at: 0,
      observed_at: "2026-11-14T12:00:00Z",
      execution_authorized: false,
      scope: "local_observation_not_atomic",
      checks: [{ code: "runtime_approval", status: "unknown" }],
    });
    await user.click(
      within(serverPanel).getByRole("button", {
        name: serverReadinessMessages.en.inspect,
      }),
    );
    expect(inspectServerReadiness).toHaveBeenCalledExactlyOnceWith({
      organizationId: "org1",
      workerId: "worker1",
      goalId: "goal1",
      planId: "plan1",
      workerVersion: 1,
      planVersion: 1,
    });
    expect(
      await within(serverPanel).findByText("2026-11-14T12:00:00Z"),
    ).toBeInTheDocument();
    expect(
      within(serverPanel).getByText(serverReadinessMessages.en.unknown),
    ).toBeInTheDocument();
    vi.mocked(inspectServerReadiness).mockRejectedValueOnce(new Error("409"));
    await user.click(
      within(serverPanel).getByRole("button", {
        name: serverReadinessMessages.en.inspect,
      }),
    );
    expect(
      await within(serverPanel).findByText(serverReadinessMessages.en.failed),
    ).toBeInTheDocument();
    expect(
      within(serverPanel).queryByText("2026-11-14T12:00:00Z"),
    ).not.toBeInTheDocument();
    expect(
      within(serverPanel).queryByText(serverReadinessMessages.en.unknown),
    ).not.toBeInTheDocument();
    expect(organizationsApi.createDigitalWorkerPlan).not.toHaveBeenCalled();
  });

  it("opens selected worker knowledge mappings read-only until a separate confirmation", async () => {
    const user = await openPlans();
    expect(workerKnowledgeMappingsApi.list).not.toHaveBeenCalled();
    expect(externalKnowledgeApi.list).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: en.knowledgeMappings.title }),
    );
    expect(
      await screen.findByText(en.knowledgeMappings.empty),
    ).toBeInTheDocument();
    expect(screen.getByText(en.knowledgeMappings.boundary)).toBeInTheDocument();
    expect(workerKnowledgeMappingsApi.list).toHaveBeenCalledWith(
      "org1",
      "worker1",
      0,
    );
    expect(externalKnowledgeApi.list).toHaveBeenCalledTimes(1);
    expect(
      screen.getByRole("button", { name: en.knowledgeMappings.create }),
    ).toBeDisabled();
    const panel = screen.getByRole("dialog");
    await user.click(
      within(panel).getByText(en.knowledgeMappings.close, { selector: "span" }),
    );
    expect(workerKnowledgeMappingsApi.create).not.toHaveBeenCalled();
    expect(workerKnowledgeMappingsApi.revoke).not.toHaveBeenCalled();
  });

  it("does not show planning for organization members", async () => {
    vi.mocked(organizationsApi.chart).mockResolvedValue({
      organization: { ...organization, member_role: "member" },
      positions: [],
      departments: [],
      members: [],
    });
    await openPlans();
    expect(
      screen.queryByRole("button", { name: en.digitalTwinPlanning.open }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: en.planningDialogue.open }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: en.knowledgeMappings.title }),
    ).not.toBeInTheDocument();
    expect(workerKnowledgeMappingsApi.list).not.toHaveBeenCalled();
    expect(externalKnowledgeApi.list).not.toHaveBeenCalled();
  });
});
