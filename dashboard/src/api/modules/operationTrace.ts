import { request } from "../request";
export const traceActions = [
  "scene.run_requested",
  "scene.run_approved",
  "scene.run_rejected",
  "scene.run.delivery_reviewed",
  "scene.mattermost_publication.proposed",
  "scene.mattermost_publication.approved_locally",
  "scene.mattermost_publication.revoked",
  "cron.run_ok",
  "cron.run_failed",
] as const;
export interface OperationTrace {
  organization_id: string;
  run_id: string;
  as_of: number;
  history_complete: false;
  execution_authorized: false;
  truncated: boolean;
  events: {
    record_id: number;
    recorded_at: number;
    action: (typeof traceActions)[number];
    actor_kind: "system" | "user_account" | "unknown";
    actor_user_id: number | null;
    interaction_mode: "service_execution" | "not_recorded";
  }[];
  execution_context: {
    agent_id: string;
    on_behalf_of_user_id: number;
    bound_cron_id: string | null;
    configured_executor: "http_inspection" | "agent_task" | "unknown";
    schedule_kind: "one_shot" | "unknown";
  };
}
export function validOperationTrace(
  data: OperationTrace,
  org: string,
  run: string,
) {
  const positive = (n: unknown) =>
    typeof n === "number" && Number.isSafeInteger(n) && n > 0;
  const context = data?.execution_context;
  return (
    !!data &&
    data.organization_id === org &&
    data.run_id === run &&
    positive(data.as_of) &&
    data.history_complete === false &&
    data.execution_authorized === false &&
    typeof data.truncated === "boolean" &&
    !!context &&
    typeof context.agent_id === "string" &&
    positive(context.on_behalf_of_user_id) &&
    (context.bound_cron_id === null ||
      typeof context.bound_cron_id === "string") &&
    ["http_inspection", "agent_task", "unknown"].includes(
      context.configured_executor,
    ) &&
    ["one_shot", "unknown"].includes(context.schedule_kind) &&
    Array.isArray(data.events) &&
    data.events.length <= 100 &&
    new Set(data.events.map((e) => e.record_id)).size === data.events.length &&
    data.events.every(
      (e) =>
        positive(e.record_id) &&
        positive(e.recorded_at) &&
        traceActions.includes(e.action) &&
        (e.actor_kind === "user_account"
          ? positive(e.actor_user_id) && e.interaction_mode === "not_recorded"
          : e.actor_user_id === null &&
            (e.actor_kind === "system"
              ? e.action.startsWith("cron.") &&
                e.interaction_mode === "service_execution"
              : e.actor_kind === "unknown" &&
                e.interaction_mode === "not_recorded")),
    )
  );
}
export const operationTraceApi = {
  read: (org: string, run: string) =>
    request<OperationTrace>(
      `/organizations/${encodeURIComponent(
        org,
      )}/scene-runs/${encodeURIComponent(run)}/operation-trace`,
      { cache: "no-store" },
    ),
};
