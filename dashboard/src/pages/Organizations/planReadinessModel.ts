import type {
  DigitalWorker,
  DigitalWorkerGoal,
  DigitalWorkerPlan,
  EnterpriseScene,
  EnterpriseSceneRun,
} from "../../api/modules/organizations";

/** Local observations only: runtime permissions and approvals remain server-owned. */
export function inspectPlanReadiness(
  worker: DigitalWorker,
  goal: DigitalWorkerGoal,
  plan: DigitalWorkerPlan,
  scenes: EnterpriseScene[],
) {
  const linked =
    goal.worker_id === worker.worker_id && plan.goal_id === goal.goal_id;
  return {
    worker: linked && worker.status === "active",
    goal: linked && goal.status === "active",
    plan: linked && plan.status === "ready",
    content:
      linked &&
      Boolean(
        plan.title.trim() &&
          plan.approach.trim() &&
          goal.success_criteria.trim() &&
          plan.steps.length &&
          plan.steps.every(
            (step) => step.action.trim() && step.expected_output.trim(),
          ),
      ),
    scene:
      linked &&
      scenes.some(
        (scene) =>
          scene.organization_id === worker.organization_id &&
          scene.position_id === worker.position_id &&
          scene.agent_id === worker.agent_id &&
          Boolean(scene.enabled),
      ),
  };
}

export function selectPlanRuns(
  worker: DigitalWorker,
  goal: DigitalWorkerGoal,
  plan: DigitalWorkerPlan,
  runs: EnterpriseSceneRun[],
) {
  if (goal.worker_id !== worker.worker_id || plan.goal_id !== goal.goal_id)
    return [];
  return runs
    .filter(
      (run) =>
        run.organization_id === worker.organization_id &&
        run.plan_id === plan.plan_id &&
        run.agent_id === worker.agent_id &&
        run.position_id === worker.position_id &&
        (!run.plan_snapshot ||
          (run.plan_snapshot.worker.worker_id === worker.worker_id &&
            run.plan_snapshot.plan.plan_id === plan.plan_id)),
    )
    .sort((a, b) => b.created_at - a.created_at);
}
