import { request } from "../request";

export const queueCategories = [
  "awaiting_approval",
  "failed",
  "unreviewed_success",
  "changes_requested",
] as const;
export type QueueCategory = (typeof queueCategories)[number];
export interface RunQueue {
  organization_id: string;
  category: QueueCategory;
  as_of: number;
  items: {
    run_id: string;
    status: "awaiting_approval" | "failed" | "succeeded";
    created_at: number;
    decision: "changes_requested" | null;
  }[];
  next_before: number | null;
  execution_authorized: false;
  persisted_state_may_lag: true;
}
export function validRunQueue(
  data: RunQueue,
  org: string,
  category: QueueCategory,
  before?: number,
) {
  const integer = (n: unknown) =>
    typeof n === "number" && Number.isSafeInteger(n) && n >= 0;
  return (
    !!data &&
    data.organization_id === org &&
    data.category === category &&
    integer(data.as_of) &&
    data.execution_authorized === false &&
    data.persisted_state_may_lag === true &&
    (data.next_before === null ||
      (integer(data.next_before) &&
        data.next_before > 0 &&
        (before === undefined || data.next_before < before))) &&
    Array.isArray(data.items) &&
    data.items.length <= 20 &&
    new Set(data.items.map((item) => item.run_id)).size === data.items.length &&
    data.items.every(
      (item) =>
        typeof item.run_id === "string" &&
        !!item.run_id &&
        integer(item.created_at) &&
        item.status ===
          (category === "awaiting_approval" || category === "failed"
            ? category
            : "succeeded") &&
        item.decision ===
          (category === "changes_requested" ? "changes_requested" : null),
    )
  );
}
export interface RunQueueDetail {
  organization_id: string;
  run_id: string;
  scene_id: string;
  scene_name: string;
  scene_version: number;
  plan_id: string | null;
  status: "awaiting_approval" | "rejected" | "queued" | "succeeded" | "failed";
  created_at: number;
  updated_at: number;
  decision: "accepted" | "changes_requested" | null;
  as_of: number;
  persisted_state_may_lag: true;
}
export const runQueueApi = {
  detail: (org: string, runId: string) =>
    request<RunQueueDetail>(
      `/organizations/${encodeURIComponent(org)}/run-queue/${encodeURIComponent(
        runId,
      )}`,
      { cache: "no-store" },
    ),
  read: (org: string, category: QueueCategory, before?: number) =>
    request<RunQueue>(
      `/organizations/${encodeURIComponent(
        org,
      )}/run-queue?category=${category}&limit=20${
        before === undefined ? "" : `&before=${before}`
      }`,
      { cache: "no-store" },
    ),
};
