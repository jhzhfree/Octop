import { request } from "../request";
export interface GoalHandoff {
  handoff_id: string;
  organization_id: string;
  source_goal_id: string;
  target_goal_id: string;
  target_worker_id: string;
  created_by: number;
  reason: string;
  source_snapshot: { title: string; worker_name: string; version: number };
  target_snapshot: { name: string; position_title: string };
  target_goal_status?: string | null;
}
export interface HandoffBody {
  request_id: string;
  expected_source_version: number;
  expected_target_version: number;
  target_worker_id: string;
  reason: string;
  title: string;
  description: string;
  success_criteria: string;
}
const root = (org: string) => `/organizations/${encodeURIComponent(org)}`;
export const goalHandoffsApi = {
  create: (org: string, worker: string, goal: string, body: HandoffBody) =>
    request<GoalHandoff>(
      `${root(org)}/digital-workers/${encodeURIComponent(
        worker,
      )}/goals/${encodeURIComponent(goal)}/handoffs`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  list: (org: string, goal?: string, before?: string) => {
    const query = new URLSearchParams();
    if (goal) query.set("goal_id", goal);
    if (before) query.set("before", before);
    return request<{ items: GoalHandoff[]; next_before: string | null }>(
      `${root(org)}/goal-handoffs?${query}`,
      { cache: "no-store" },
    );
  },
};
