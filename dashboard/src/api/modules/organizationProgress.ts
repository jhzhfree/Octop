import { request } from "../request";

export const executionKeys = [
  "awaiting_approval",
  "rejected",
  "queued",
  "succeeded",
  "failed",
] as const;
export const deliveryKeys = [
  "unreviewed_success",
  "accepted",
  "changes_requested",
] as const;
export interface OrganizationProgress {
  organization_id: string;
  window_start: number;
  window_end_exclusive: number;
  as_of: number;
  scope: "persisted_runs_by_creation_time";
  unit: "run";
  max_runs: number;
  limit_exceeded: boolean;
  denominator: number | null;
  counts: {
    execution: Record<(typeof executionKeys)[number], number>;
    delivery: Record<(typeof deliveryKeys)[number], number>;
  } | null;
  inconsistent_records: number | null;
  persisted_state_may_lag: true;
  external_systems_verified: false;
  execution_authorized: false;
}
const count = (n: unknown) =>
  typeof n === "number" && Number.isSafeInteger(n) && n >= 0;
export function validProgress(
  r: OrganizationProgress,
  org: string,
  start: number,
  end: number,
) {
  if (
    !r ||
    r.organization_id !== org ||
    r.window_start !== start ||
    r.window_end_exclusive !== end ||
    !count(r.as_of) ||
    r.scope !== "persisted_runs_by_creation_time" ||
    r.unit !== "run" ||
    r.persisted_state_may_lag !== true ||
    r.external_systems_verified !== false ||
    r.execution_authorized !== false ||
    !count(r.max_runs)
  )
    return false;
  if (r.limit_exceeded === true)
    return r.counts === null && r.denominator === null;
  return (
    r.limit_exceeded === false &&
    count(r.denominator) &&
    count(r.inconsistent_records) &&
    !!r.counts &&
    executionKeys.every((k) => count(r.counts?.execution?.[k])) &&
    deliveryKeys.every((k) => count(r.counts?.delivery?.[k]))
  );
}
export const organizationProgressApi = {
  read: (org: string, start: number, end: number) =>
    request<OrganizationProgress>(
      `/organizations/${encodeURIComponent(
        org,
      )}/progress?start=${start}&end=${end}`,
      { cache: "no-store" },
    ),
};
