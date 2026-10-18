import { request } from "../request";

export interface OrganizationRecord {
  organization_id: string;
  owner_user_id: number;
  name: string;
  description: string;
  member_role?: "owner" | "manager" | "member";
  created_at: number;
  updated_at: number;
}
export interface OrganizationMember {
  organization_id: string;
  user_id: number;
  role: "owner" | "manager" | "member";
  username: string;
  display_name: string | null;
  disabled: number;
}
export interface OrganizationDepartment {
  department_id: string;
  organization_id: string;
  parent_department_id: string | null;
  name: string;
  code: string;
  description: string;
  sort_order: number;
}
export interface OrganizationAppointment {
  assignment_id: string;
  position_id: string;
  subject_type: "user" | "agent";
  subject_id: string;
  label: string;
}
export interface OrganizationPosition {
  position_id: string;
  organization_id: string;
  department_id: string | null;
  reports_to_position_id: string | null;
  title: string;
  code: string;
  duties: string;
  capabilities: string[];
  assignments: OrganizationAppointment[];
}
export interface OrganizationChart {
  organization: OrganizationRecord;
  members: OrganizationMember[];
  departments: OrganizationDepartment[];
  positions: OrganizationPosition[];
}
export interface OrganizationWriteBody {
  name: string;
  description?: string;
}
export interface DepartmentWriteBody {
  name: string;
  code?: string;
  description?: string;
  parent_department_id?: string | null;
  sort_order?: number;
}
export interface PositionWriteBody {
  title: string;
  code?: string;
  department_id?: string | null;
  reports_to_position_id?: string | null;
  duties?: string;
  capabilities?: string[];
}
export interface EnterpriseScene {
  scene_id: string;
  organization_id: string;
  position_id: string;
  agent_id: string;
  owner_user_id: number;
  version: number;
  name: string;
  prompt: string;
  mcp_servers: string[];
  enabled: number | boolean;
  created_at: number;
  updated_at: number;
}
export interface EnterpriseSceneVersion {
  scene_id: string;
  version: number;
  name: string;
  prompt: string;
  mcp_servers: string[];
  enabled: number | boolean;
  created_by: number;
  created_at: number;
}
export interface EnterpriseSceneRun {
  run_id: string;
  scene_id: string;
  organization_id: string;
  position_id: string;
  agent_id: string;
  scene_name: string;
  scene_version: number;
  prompt_snapshot: string;
  mcp_servers: string[];
  requested_by: number;
  status: "awaiting_approval" | "rejected" | "queued" | "succeeded" | "failed";
  approved_by: number | null;
  decision_note: string;
  cron_id: string | null;
  error_summary: string | null;
  plan_id?: string | null;
  plan_snapshot?: {
    worker: {
      worker_id: string;
      name: string;
      responsibilities: string;
      capability_version: number;
    };
    capabilities?: {
      skills: string[];
      knowledge: string[];
      operating_guidance: string[];
      skill_package_ids: string[];
      knowledge_base_ids: string[];
    };
    goal: {
      goal_id: string;
      title: string;
      description: string;
      success_criteria: string;
      period_start: string | null;
      period_end: string | null;
    };
    plan: {
      plan_id: string;
      title: string;
      approach: string;
      steps: DigitalWorkerPlanStep[];
      version: number;
    };
  } | null;
  created_at: number;
  updated_at: number;
}
export interface EnterpriseSceneRunEvidence {
  run_id: string;
  available: boolean;
  answer: string | null;
}
export interface EnterpriseSceneWriteBody {
  name: string;
  position_id: string;
  agent_id: string;
  prompt: string;
  mcp_servers: string[];
}
export interface DigitalWorker {
  worker_id: string;
  organization_id: string;
  position_id: string;
  agent_id: string;
  owner_user_id: number;
  position_title: string;
  agent_name: string;
  name: string;
  responsibilities: string;
  capabilities: {
    skills: string[];
    knowledge: string[];
    operating_guidance: string[];
    skill_package_ids: string[];
    knowledge_base_ids: string[];
  };
  capability_version: number;
  status: "draft" | "active" | "paused" | "archived";
  version: number;
  created_at: number;
  updated_at: number;
}
export interface DigitalWorkerGoal {
  goal_id: string;
  worker_id: string;
  title: string;
  description: string;
  success_criteria: string;
  period_start: string | null;
  period_end: string | null;
  status: "draft" | "active" | "completed" | "cancelled";
  created_by: number;
  created_at: number;
  updated_at: number;
}
export interface DigitalWorkerPlanStep {
  action: string;
  expected_output: string;
}
export interface DigitalWorkerPlan {
  plan_id: string;
  goal_id: string;
  title: string;
  approach: string;
  steps: DigitalWorkerPlanStep[];
  status: "draft" | "ready" | "cancelled";
  version: number;
  created_by: number;
  created_at: number;
  updated_at: number;
}
export interface DigitalWorkerCapabilityResource {
  id: string;
  name: string;
}
export interface DigitalWorkerAuthorizedResources {
  skills: DigitalWorkerCapabilityResource[];
  knowledge_bases: DigitalWorkerCapabilityResource[];
}
export interface DigitalWorkerCapabilityChanges {
  skills: string[];
  knowledge: string[];
  operating_guidance: string[];
  skill_package_ids: string[];
  knowledge_base_ids: string[];
}
export interface DigitalWorkerCapabilityProposal {
  proposal_id: string;
  worker_id: string;
  source_run_id: string;
  source_summary: string;
  observed_gaps: string;
  proposed_changes: DigitalWorkerCapabilityChanges;
  expected_benefit: string;
  risk_notes: string;
  base_capability_version: number;
  status: "submitted" | "published" | "rejected";
  created_by: number;
  reviewed_by: number | null;
  review_note: string;
  created_at: number;
  updated_at: number;
}
export interface DigitalWorkerCapabilityProposalCreateBody {
  source_run_id: string;
  source_summary: string;
  observed_gaps: string;
  proposed_changes: DigitalWorkerCapabilityChanges;
  expected_benefit: string;
  risk_notes: string;
}

export const organizationsApi = {
  list: () => request<OrganizationRecord[]>("/organizations"),
  create: (body: OrganizationWriteBody) =>
    request<OrganizationRecord>("/organizations", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  chart: (id: string) =>
    request<OrganizationChart>(
      "/organizations/" + encodeURIComponent(id) + "/chart",
    ),
  update: (id: string, body: Partial<OrganizationWriteBody>) =>
    request<OrganizationRecord>("/organizations/" + encodeURIComponent(id), {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  remove: (id: string) =>
    request<void>("/organizations/" + encodeURIComponent(id), {
      method: "DELETE",
    }),
  createDepartment: (id: string, body: DepartmentWriteBody) =>
    request<OrganizationDepartment>(
      "/organizations/" + encodeURIComponent(id) + "/departments",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    ),
  updateDepartment: (
    id: string,
    deptId: string,
    body: Partial<DepartmentWriteBody>,
  ) =>
    request<OrganizationDepartment>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/departments/" +
        encodeURIComponent(deptId),
      { method: "PATCH", body: JSON.stringify(body) },
    ),
  deleteDepartment: (id: string, deptId: string) =>
    request<void>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/departments/" +
        encodeURIComponent(deptId),
      { method: "DELETE" },
    ),
  createPosition: (id: string, body: PositionWriteBody) =>
    request<OrganizationPosition>(
      "/organizations/" + encodeURIComponent(id) + "/positions",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    ),
  updatePosition: (
    id: string,
    positionId: string,
    body: Partial<PositionWriteBody>,
  ) =>
    request<OrganizationPosition>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/positions/" +
        encodeURIComponent(positionId),
      { method: "PATCH", body: JSON.stringify(body) },
    ),
  deletePosition: (id: string, positionId: string) =>
    request<void>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/positions/" +
        encodeURIComponent(positionId),
      { method: "DELETE" },
    ),
  addMember: (id: string, userId: number, role: "manager" | "member") =>
    request<OrganizationMember>(
      "/organizations/" + encodeURIComponent(id) + "/members",
      {
        method: "POST",
        body: JSON.stringify({ user_id: userId, role }),
      },
    ),
  removeMember: (id: string, userId: number) =>
    request<void>(
      "/organizations/" + encodeURIComponent(id) + "/members/" + userId,
      { method: "DELETE" },
    ),
  assign: (
    id: string,
    positionId: string,
    subjectType: "user" | "agent",
    subjectId: string,
  ) =>
    request<OrganizationAppointment>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/positions/" +
        encodeURIComponent(positionId) +
        "/appointments",
      {
        method: "POST",
        body: JSON.stringify({
          subject_type: subjectType,
          subject_id: subjectId,
        }),
      },
    ),
  unassign: (id: string, assignmentId: string) =>
    request<void>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/appointments/" +
        encodeURIComponent(assignmentId),
      { method: "DELETE" },
    ),
  digitalWorkers: (id: string) =>
    request<DigitalWorker[]>(
      "/organizations/" + encodeURIComponent(id) + "/digital-workers",
    ),
  createDigitalWorker: (
    id: string,
    body: Pick<DigitalWorker, "position_id" | "agent_id" | "name"> & {
      responsibilities?: string;
    },
  ) =>
    request<DigitalWorker>(
      "/organizations/" + encodeURIComponent(id) + "/digital-workers",
      { method: "POST", body: JSON.stringify(body) },
    ),
  updateDigitalWorker: (
    id: string,
    workerId: string,
    expected_version: number,
    body: Partial<Pick<DigitalWorker, "name" | "responsibilities" | "status">>,
  ) =>
    request<DigitalWorker>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/digital-workers/" +
        encodeURIComponent(workerId),
      { method: "PATCH", body: JSON.stringify({ ...body, expected_version }) },
    ),
  digitalWorkerGoals: (id: string, workerId: string) =>
    request<DigitalWorkerGoal[]>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/digital-workers/" +
        encodeURIComponent(workerId) +
        "/goals",
    ),
  createDigitalWorkerGoal: (
    id: string,
    workerId: string,
    body: Pick<DigitalWorkerGoal, "title" | "success_criteria"> & {
      description?: string;
      period_start?: string;
      period_end?: string;
    },
  ) =>
    request<DigitalWorkerGoal>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/digital-workers/" +
        encodeURIComponent(workerId) +
        "/goals",
      { method: "POST", body: JSON.stringify(body) },
    ),
  updateDigitalWorkerGoal: (
    id: string,
    workerId: string,
    goalId: string,
    body: Partial<
      Pick<
        DigitalWorkerGoal,
        | "title"
        | "description"
        | "success_criteria"
        | "period_start"
        | "period_end"
        | "status"
      >
    >,
  ) =>
    request<DigitalWorkerGoal>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/digital-workers/" +
        encodeURIComponent(workerId) +
        "/goals/" +
        encodeURIComponent(goalId),
      { method: "PATCH", body: JSON.stringify(body) },
    ),
  digitalWorkerPlans: (id: string, workerId: string, goalId: string) =>
    request<DigitalWorkerPlan[]>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/digital-workers/" +
        encodeURIComponent(workerId) +
        "/goals/" +
        encodeURIComponent(goalId) +
        "/plans",
    ),
  createDigitalWorkerPlan: (
    id: string,
    workerId: string,
    goalId: string,
    body: Pick<DigitalWorkerPlan, "title" | "approach" | "steps">,
  ) =>
    request<DigitalWorkerPlan>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/digital-workers/" +
        encodeURIComponent(workerId) +
        "/goals/" +
        encodeURIComponent(goalId) +
        "/plans",
      { method: "POST", body: JSON.stringify(body) },
    ),
  updateDigitalWorkerPlan: (
    id: string,
    workerId: string,
    goalId: string,
    planId: string,
    expected_version: number,
    body: Partial<
      Pick<DigitalWorkerPlan, "title" | "approach" | "steps" | "status">
    >,
  ) =>
    request<DigitalWorkerPlan>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/digital-workers/" +
        encodeURIComponent(workerId) +
        "/goals/" +
        encodeURIComponent(goalId) +
        "/plans/" +
        encodeURIComponent(planId),
      { method: "PATCH", body: JSON.stringify({ ...body, expected_version }) },
    ),
  digitalWorkerAuthorizedResources: (id: string, workerId: string) =>
    request<DigitalWorkerAuthorizedResources>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/digital-workers/" +
        encodeURIComponent(workerId) +
        "/authorized-resources",
    ),
  digitalWorkerCapabilityProposals: (id: string, workerId: string) =>
    request<DigitalWorkerCapabilityProposal[]>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/digital-workers/" +
        encodeURIComponent(workerId) +
        "/capability-proposals",
    ),
  createDigitalWorkerCapabilityProposal: (
    id: string,
    workerId: string,
    body: DigitalWorkerCapabilityProposalCreateBody,
  ) =>
    request<DigitalWorkerCapabilityProposal>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/digital-workers/" +
        encodeURIComponent(workerId) +
        "/capability-proposals",
      { method: "POST", body: JSON.stringify(body) },
    ),
  reviewDigitalWorkerCapabilityProposal: (
    id: string,
    workerId: string,
    proposalId: string,
    approve: boolean,
    review_note: string,
  ) =>
    request<DigitalWorkerCapabilityProposal>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/digital-workers/" +
        encodeURIComponent(workerId) +
        "/capability-proposals/" +
        encodeURIComponent(proposalId) +
        "/review",
      { method: "POST", body: JSON.stringify({ approve, review_note }) },
    ),
  scenes: (id: string) =>
    request<EnterpriseScene[]>(
      "/organizations/" + encodeURIComponent(id) + "/scenes",
    ),
  createScene: (id: string, body: EnterpriseSceneWriteBody) =>
    request<EnterpriseScene>(
      "/organizations/" + encodeURIComponent(id) + "/scenes",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    ),
  updateScene: (
    id: string,
    sceneId: string,
    expected_version: number,
    body: Partial<
      Pick<EnterpriseScene, "name" | "prompt" | "mcp_servers" | "enabled">
    >,
  ) =>
    request<EnterpriseScene>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/scenes/" +
        encodeURIComponent(sceneId),
      {
        method: "PATCH",
        body: JSON.stringify({ ...body, expected_version }),
      },
    ),
  sceneVersions: (id: string, sceneId: string) =>
    request<EnterpriseSceneVersion[]>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/scenes/" +
        encodeURIComponent(sceneId) +
        "/versions",
    ),
  requestSceneRun: (id: string, sceneId: string, planId?: string) =>
    request<EnterpriseSceneRun>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/scenes/" +
        encodeURIComponent(sceneId) +
        "/runs",
      planId
        ? { method: "POST", body: JSON.stringify({ plan_id: planId }) }
        : { method: "POST" },
    ),
  sceneRuns: (id: string) =>
    request<EnterpriseSceneRun[]>(
      "/organizations/" + encodeURIComponent(id) + "/scene-runs",
    ),
  sceneRunEvidence: (id: string, runId: string) =>
    request<EnterpriseSceneRunEvidence>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/scene-runs/" +
        encodeURIComponent(runId) +
        "/evidence",
    ),
  decideSceneRun: (
    id: string,
    runId: string,
    decision: "approve" | "reject",
    note = "",
  ) =>
    request<EnterpriseSceneRun>(
      "/organizations/" +
        encodeURIComponent(id) +
        "/scene-runs/" +
        encodeURIComponent(runId) +
        "/" +
        decision,
      {
        method: "POST",
        body: JSON.stringify({ note }),
      },
    ),
};
