import { request } from "../request";

export interface ReadinessTarget {
  organizationId: string;
  workerId: string;
  goalId: string;
  planId: string;
  workerVersion: number;
  planVersion: number;
}
export interface ServerReadinessReport {
  organization_id: string;
  worker_id: string;
  goal_id: string;
  plan_id: string;
  worker_version: number;
  capability_version: number;
  plan_version: number;
  goal_updated_at: number;
  observed_at: string;
  execution_authorized: false;
  scope: "local_observation_not_atomic";
  checks: { code: string; status: "satisfied" | "unmet" | "unknown" }[];
}
export function inspectServerReadiness(target: ReadinessTarget) {
  const ids = [
    target.organizationId,
    target.workerId,
    target.goalId,
    target.planId,
  ].map(encodeURIComponent);
  const query = new URLSearchParams({
    expected_worker_version: String(target.workerVersion),
    expected_plan_version: String(target.planVersion),
  });
  return request<ServerReadinessReport>(
    `/organizations/${ids[0]}/digital-workers/${ids[1]}/goals/${ids[2]}/plans/${ids[3]}/readiness?${query}`,
    { cache: "no-store" },
  );
}
export function matchesReadinessTarget(
  report: ServerReadinessReport,
  target: ReadinessTarget,
) {
  return (
    report.organization_id === target.organizationId &&
    report.worker_id === target.workerId &&
    report.goal_id === target.goalId &&
    report.plan_id === target.planId &&
    report.worker_version === target.workerVersion &&
    report.plan_version === target.planVersion &&
    report.execution_authorized === false &&
    report.scope === "local_observation_not_atomic"
  );
}
