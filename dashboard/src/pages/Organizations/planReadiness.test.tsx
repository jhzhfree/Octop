import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type {
  DigitalWorker,
  DigitalWorkerGoal,
  DigitalWorkerPlan,
  EnterpriseScene,
  EnterpriseSceneRun,
} from "../../api/modules/organizations";
import { inspectPlanReadiness, selectPlanRuns } from "./planReadiness";
import PlanReadiness from "./PlanReadiness";
import en from "../../locales/en.json";

vi.mock("react-i18next", async () => {
  const { createInstance } = await import("i18next");
  const { default: messages } = await import("../../locales/en.json");
  const instance = createInstance();
  await instance.init({
    lng: "en",
    resources: { en: { translation: messages } },
  });
  return { useTranslation: () => ({ t: instance.t.bind(instance) }) };
});
const worker: DigitalWorker = {
  worker_id: "w",
  organization_id: "o",
  position_id: "p",
  agent_id: "a",
  owner_user_id: 1,
  position_title: "Research",
  agent_name: "Agent",
  name: "Twin",
  responsibilities: "Research",
  capabilities: {
    skills: [],
    knowledge: [],
    operating_guidance: [],
    skill_package_ids: [],
    knowledge_base_ids: [],
  },
  capability_version: 1,
  status: "active",
  version: 1,
  created_at: 0,
  updated_at: 0,
};
const goal: DigitalWorkerGoal = {
  goal_id: "g",
  worker_id: "w",
  title: "Report",
  description: "",
  success_criteria: "Sources cited",
  period_start: null,
  period_end: null,
  status: "active",
  created_by: 1,
  created_at: 0,
  updated_at: 0,
};
const plan: DigitalWorkerPlan = {
  plan_id: "plan",
  goal_id: "g",
  title: "Report",
  approach: "Read approved sources",
  steps: [{ action: "Read", expected_output: "Evidence" }],
  status: "ready",
  version: 2,
  created_by: 1,
  created_at: 0,
  updated_at: 0,
};
const scene: EnterpriseScene = {
  scene_id: "s",
  organization_id: "o",
  position_id: "p",
  agent_id: "a",
  owner_user_id: 1,
  version: 1,
  name: "Research",
  prompt: "Research",
  mcp_servers: [],
  enabled: true,
  created_at: 0,
  updated_at: 0,
};
const run: EnterpriseSceneRun = {
  run_id: "run1",
  scene_id: "s",
  organization_id: "o",
  position_id: "p",
  agent_id: "a",
  scene_name: "Research",
  scene_version: 1,
  prompt_snapshot: "",
  mcp_servers: [],
  requested_by: 1,
  status: "awaiting_approval",
  approved_by: null,
  decision_note: "",
  cron_id: null,
  error_summary: null,
  plan_id: "plan",
  created_at: 1,
  updated_at: 1,
};
const snapshot = (
  version: number,
): NonNullable<EnterpriseSceneRun["plan_snapshot"]> => ({
  worker: {
    worker_id: "w",
    name: "Twin",
    responsibilities: "Research",
    capability_version: 1,
  },
  goal,
  plan: { ...plan, version },
});

describe("read-only plan preparation", () => {
  it("reports only observed local conditions", () => {
    expect(inspectPlanReadiness(worker, goal, plan, [scene])).toEqual({
      worker: true,
      goal: true,
      plan: true,
      content: true,
      scene: true,
    });
    expect(
      inspectPlanReadiness(
        { ...worker, status: "paused" },
        { ...goal, status: "completed" },
        { ...plan, status: "draft", steps: [] },
        [],
      ),
    ).toEqual({
      worker: false,
      goal: false,
      plan: false,
      content: false,
      scene: false,
    });
  });
  it("does not accept mismatched or disabled scenes and blank content", () => {
    expect(
      inspectPlanReadiness(
        worker,
        goal,
        { ...plan, steps: [{ action: " ", expected_output: "Evidence" }] },
        [
          { ...scene, enabled: false },
          { ...scene, organization_id: "other" },
        ],
      ),
    ).toMatchObject({ content: false, scene: false });
    expect(
      Object.values(
        inspectPlanReadiness(worker, { ...goal, worker_id: "other" }, plan, [
          scene,
        ]),
      ),
    ).not.toContain(true);
  });
  it("filters run scope and preserves source order", () => {
    const data = [
      run,
      { ...run, run_id: "latest", created_at: 9 },
      { ...run, run_id: "otherOrg", organization_id: "other" },
      { ...run, run_id: "otherPlan", plan_id: "other" },
      {
        ...run,
        run_id: "otherWorker",
        plan_snapshot: {
          ...snapshot(2),
          worker: { ...snapshot(2).worker, worker_id: "other" },
        },
      },
    ];
    expect(
      selectPlanRuns(worker, goal, plan, data).map((r) => r.run_id),
    ).toEqual(["latest", "run1"]);
    expect(data[0].run_id).toBe("run1");
    expect(
      selectPlanRuns(worker, { ...goal, goal_id: "other" }, plan, data),
    ).toEqual([]);
  });
  it("labels current, other and unknown versions without authorizing actions", () => {
    render(
      <PlanReadiness
        worker={worker}
        goal={goal}
        plan={plan}
        scenes={[scene]}
        runs={[
          run,
          {
            ...run,
            run_id: "old",
            status: "succeeded",
            plan_snapshot: snapshot(1),
          },
          {
            ...run,
            run_id: "new",
            status: "failed",
            plan_snapshot: snapshot(2),
          },
          { ...run, run_id: "higher", plan_snapshot: snapshot(3) },
        ]}
      />,
    );
    expect(
      screen.getByText(en.planReadiness.unknownVersion),
    ).toBeInTheDocument();
    expect(screen.getByText("Other plan version v1")).toBeInTheDocument();
    expect(screen.getByText("Current plan v2")).toBeInTheDocument();
    expect(screen.getByText("Other plan version v3")).toBeInTheDocument();
    expect(
      screen.getByText(en.organizations.runStatuses.succeeded),
    ).toBeInTheDocument();
    expect(screen.getByText(en.planReadiness.runBoundary)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
  it("does not infer completed work from missing records", () => {
    render(
      <PlanReadiness
        worker={worker}
        goal={goal}
        plan={plan}
        scenes={[]}
        runs={[]}
      />,
    );
    expect(screen.getByText(en.planReadiness.noRuns)).toBeInTheDocument();
    expect(screen.getByText(en.planReadiness.missing)).toBeInTheDocument();
  });
});
