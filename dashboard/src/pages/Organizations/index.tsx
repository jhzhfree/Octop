import {
  Building2,
  BriefcaseBusiness,
  Check,
  ClipboardList,
  Pencil,
  Play,
  Plus,
  Target,
  Trash2,
  UserRoundPlus,
  UserRoundX,
  X,
} from "lucide-react";
import {
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import {
  organizationsApi,
  type OrganizationChart,
  type OrganizationDepartment,
  type EnterpriseScene,
  type EnterpriseSceneRun,
  type EnterpriseSceneVersion,
  type OrganizationPosition,
  type OrganizationRecord,
  type DigitalWorker,
  type DigitalWorkerGoal,
  type DigitalWorkerPlan,
  type DigitalWorkerAuthorizedResources,
  type DigitalWorkerCapabilityProposal,
} from "../../api/modules/organizations";

type ModalKind =
  | "organization"
  | "department"
  | "position"
  | "member"
  | "appointment";
type AssignmentKind = "user" | "agent";

export default function OrganizationsPage() {
  const { t } = useTranslation();
  const timezone = useServerTimezone();
  const currentUser = useCurrentUser();
  const [form] = Form.useForm();
  const [sceneForm] = Form.useForm();
  const [workerForm] = Form.useForm();
  const [goalForm] = Form.useForm();
  const [planForm] = Form.useForm();
  const [runRequestForm] = Form.useForm();
  const [capabilityProposalForm] = Form.useForm();
  const [organizations, setOrganizations] = useState<OrganizationRecord[]>([]);
  const [organizationId, setOrganizationId] = useState<string>();
  const [chart, setChart] = useState<OrganizationChart>();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [modalKind, setModalKind] = useState<ModalKind>();
  const [editingDepartment, setEditingDepartment] =
    useState<OrganizationDepartment>();
  const [editingPosition, setEditingPosition] =
    useState<OrganizationPosition>();
  const [assignPositionId, setAssignPositionId] = useState<string>();
  const [scenes, setScenes] = useState<EnterpriseScene[]>([]);
  const [sceneRuns, setSceneRuns] = useState<EnterpriseSceneRun[]>([]);
  const [sceneEvidence, setSceneEvidence] = useState<
    Record<string, string | null>
  >({});
  const [loadingEvidence, setLoadingEvidence] = useState<string>();
  const [sceneModalOpen, setSceneModalOpen] = useState(false);
  const [editingScene, setEditingScene] = useState<EnterpriseScene>();
  const [sceneVersions, setSceneVersions] = useState<EnterpriseSceneVersion[]>(
    [],
  );
  const [versionsModalScene, setVersionsModalScene] =
    useState<EnterpriseScene>();
  const [digitalWorkers, setDigitalWorkers] = useState<DigitalWorker[]>([]);
  const [workerModalOpen, setWorkerModalOpen] = useState(false);
  const [selectedWorker, setSelectedWorker] = useState<DigitalWorker>();
  const [workerGoals, setWorkerGoals] = useState<DigitalWorkerGoal[]>([]);
  const [selectedGoal, setSelectedGoal] = useState<DigitalWorkerGoal>();
  const [workerPlans, setWorkerPlans] = useState<DigitalWorkerPlan[]>([]);
  const [capabilityProposals, setCapabilityProposals] = useState<
    DigitalWorkerCapabilityProposal[]
  >([]);
  const [authorizedWorkerResources, setAuthorizedWorkerResources] =
    useState<DigitalWorkerAuthorizedResources>({
      skills: [],
      knowledge_bases: [],
    });
  const [capabilityProposalModalOpen, setCapabilityProposalModalOpen] =
    useState(false);
  const [capabilityProposalRunId, setCapabilityProposalRunId] =
    useState<string>();
  const [planModalOpen, setPlanModalOpen] = useState(false);
  const [editingPlan, setEditingPlan] = useState<DigitalWorkerPlan>();
  const [runRequestPlan, setRunRequestPlan] = useState<DigitalWorkerPlan>();
  const [runRequestOpen, setRunRequestOpen] = useState(false);
  const [goalModalOpen, setGoalModalOpen] = useState(false);
  const selectedWorkerId = selectedWorker?.worker_id;
  const selectedGoalId = selectedGoal?.goal_id;

  const load = useCallback(
    async (preferredId?: string) => {
      setLoading(true);
      try {
        const rows = await organizationsApi.list();
        setOrganizations(rows);
        const selected =
          rows.find(
            (row) => row.organization_id === (preferredId ?? organizationId),
          ) ?? rows[0];
        setOrganizationId(selected?.organization_id);
        if (selected) {
          const [nextChart, nextScenes, nextRuns, nextWorkers] =
            await Promise.all([
              organizationsApi.chart(selected.organization_id),
              organizationsApi.scenes(selected.organization_id),
              organizationsApi.sceneRuns(selected.organization_id),
              organizationsApi.digitalWorkers(selected.organization_id),
            ]);
          setChart(nextChart);
          setScenes(nextScenes);
          setSceneRuns(nextRuns);
          setDigitalWorkers(nextWorkers);
          setSelectedWorker((current) =>
            nextWorkers.find(
              (worker) => worker.worker_id === current?.worker_id,
            ),
          );
        } else {
          setChart(undefined);
          setScenes([]);
          setSceneRuns([]);
          setDigitalWorkers([]);
          setSelectedWorker(undefined);
          setWorkerGoals([]);
          setSelectedGoal(undefined);
          setWorkerPlans([]);
        }
      } catch {
        message.error(t("organizations.loadFailed"));
      } finally {
        setLoading(false);
      }
    },
    [organizationId, t],
  );

  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (!organizationId) return;
    const timer = window.setInterval(() => {
      void organizationsApi
        .sceneRuns(organizationId)
        .then(setSceneRuns)
        .catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [organizationId]);
  useEffect(() => {
    if (!organizationId || !selectedWorkerId) {
      setWorkerGoals([]);
      setCapabilityProposals([]);
      setAuthorizedWorkerResources({ skills: [], knowledge_bases: [] });
      return;
    }
    setCapabilityProposalRunId(undefined);
    void organizationsApi
      .digitalWorkerGoals(organizationId, selectedWorkerId)
      .then(setWorkerGoals)
      .catch(() => message.error(t("organizations.operationFailed")));
    void organizationsApi
      .digitalWorkerCapabilityProposals(organizationId, selectedWorkerId)
      .then(setCapabilityProposals)
      .catch(() => message.error(t("organizations.operationFailed")));
    void organizationsApi
      .digitalWorkerAuthorizedResources(organizationId, selectedWorkerId)
      .then(setAuthorizedWorkerResources)
      .catch(() =>
        setAuthorizedWorkerResources({ skills: [], knowledge_bases: [] }),
      );
  }, [organizationId, selectedWorkerId, t]);
  useEffect(() => {
    if (!organizationId || !selectedWorkerId || !selectedGoalId) {
      setWorkerPlans([]);
      return;
    }
    void organizationsApi
      .digitalWorkerPlans(organizationId, selectedWorkerId, selectedGoalId)
      .then(setWorkerPlans)
      .catch(() => message.error(t("organizations.operationFailed")));
  }, [organizationId, selectedWorkerId, selectedGoalId, t]);

  const canManage =
    chart?.organization.member_role === "owner" ||
    chart?.organization.member_role === "manager";
  const canManageMembers = chart?.organization.member_role === "owner";
  const departmentNames = useMemo(
    () =>
      new Map(
        chart?.departments.map((row) => [row.department_id, row.name]) ?? [],
      ),
    [chart?.departments],
  );
  const positionNames = useMemo(
    () =>
      new Map(
        chart?.positions.map((row) => [row.position_id, row.title]) ?? [],
      ),
    [chart?.positions],
  );
  const scenePosition = Form.useWatch("position_id", sceneForm) as
    | string
    | undefined;
  const workerPosition = Form.useWatch("position_id", workerForm) as
    | string
    | undefined;
  const assignedAgents = useMemo(
    () =>
      chart?.positions
        .filter(
          (position) =>
            !scenePosition || position.position_id === scenePosition,
        )
        .flatMap((position) =>
          position.assignments
            .filter((item) => item.subject_type === "agent")
            .map((item) => ({
              value: item.subject_id,
              label: `${item.label} · ${position.title}`,
            })),
        ) ?? [],
    [chart?.positions, scenePosition],
  );
  const workerAgents = useMemo(
    () =>
      chart?.positions
        .filter(
          (position) =>
            !workerPosition || position.position_id === workerPosition,
        )
        .flatMap((position) =>
          position.assignments
            .filter((item) => item.subject_type === "agent")
            .map((item) => ({
              value: item.subject_id,
              label: `${item.label} · ${position.title}`,
            })),
        ) ?? [],
    [chart?.positions, workerPosition],
  );

  function openWorkerModal() {
    workerForm.resetFields();
    setWorkerModalOpen(true);
  }

  async function saveWorker(values: Record<string, unknown>) {
    if (!organizationId) return;
    setSaving(true);
    try {
      await organizationsApi.createDigitalWorker(organizationId, {
        position_id: String(values.position_id ?? ""),
        agent_id: String(values.agent_id ?? ""),
        name: String(values.name ?? "").trim(),
        responsibilities: String(values.responsibilities ?? "").trim(),
      });
      setWorkerModalOpen(false);
      await load(organizationId);
      message.success(t("organizations.saved"));
    } catch {
      message.error(t("organizations.operationFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function changeWorkerStatus(worker: DigitalWorker) {
    if (!organizationId) return;
    const status = worker.status === "active" ? "paused" : "active";
    try {
      await organizationsApi.updateDigitalWorker(
        organizationId,
        worker.worker_id,
        worker.version,
        { status },
      );
      await load(organizationId);
      message.success(t("organizations.saved"));
    } catch {
      message.error(t("organizations.operationFailed"));
    }
  }

  async function saveGoal(values: Record<string, unknown>) {
    if (!organizationId || !selectedWorker) return;
    setSaving(true);
    try {
      await organizationsApi.createDigitalWorkerGoal(
        organizationId,
        selectedWorker.worker_id,
        {
          title: String(values.title ?? "").trim(),
          description: String(values.description ?? "").trim(),
          success_criteria: String(values.success_criteria ?? "").trim(),
          period_start: String(values.period_start ?? "") || undefined,
          period_end: String(values.period_end ?? "") || undefined,
        },
      );
      setGoalModalOpen(false);
      goalForm.resetFields();
      setWorkerGoals(
        await organizationsApi.digitalWorkerGoals(
          organizationId,
          selectedWorker.worker_id,
        ),
      );
      message.success(t("organizations.saved"));
    } catch {
      message.error(t("organizations.operationFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function changeGoalStatus(goal: DigitalWorkerGoal) {
    if (!organizationId || !selectedWorker) return;
    const status = goal.status === "draft" ? "active" : "completed";
    try {
      await organizationsApi.updateDigitalWorkerGoal(
        organizationId,
        selectedWorker.worker_id,
        goal.goal_id,
        { status },
      );
      setWorkerGoals(
        await organizationsApi.digitalWorkerGoals(
          organizationId,
          selectedWorker.worker_id,
        ),
      );
    } catch {
      message.error(t("organizations.operationFailed"));
    }
  }

  function openPlanModal(plan?: DigitalWorkerPlan) {
    if (!selectedGoal) return;
    setEditingPlan(plan);
    planForm.resetFields();
    planForm.setFieldsValue(
      plan
        ? { title: plan.title, approach: plan.approach, steps: plan.steps }
        : { steps: [{ action: "", expected_output: "" }] },
    );
    setPlanModalOpen(true);
  }

  async function savePlan(values: Record<string, unknown>) {
    if (!organizationId || !selectedWorker || !selectedGoal) return;
    setSaving(true);
    try {
      const body = {
        title: String(values.title ?? "").trim(),
        approach: String(values.approach ?? "").trim(),
        steps: (Array.isArray(values.steps) ? values.steps : []).map(
          (step: { action?: string; expected_output?: string }) => ({
            action: String(step.action ?? "").trim(),
            expected_output: String(step.expected_output ?? "").trim(),
          }),
        ),
      };
      if (editingPlan) {
        await organizationsApi.updateDigitalWorkerPlan(
          organizationId,
          selectedWorker.worker_id,
          selectedGoal.goal_id,
          editingPlan.plan_id,
          editingPlan.version,
          body,
        );
      } else {
        await organizationsApi.createDigitalWorkerPlan(
          organizationId,
          selectedWorker.worker_id,
          selectedGoal.goal_id,
          body,
        );
      }
      setPlanModalOpen(false);
      setEditingPlan(undefined);
      setWorkerPlans(
        await organizationsApi.digitalWorkerPlans(
          organizationId,
          selectedWorker.worker_id,
          selectedGoal.goal_id,
        ),
      );
      message.success(t("organizations.saved"));
    } catch {
      message.error(t("organizations.operationFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function changePlanStatus(plan: DigitalWorkerPlan) {
    if (!organizationId || !selectedWorker || !selectedGoal) return;
    try {
      await organizationsApi.updateDigitalWorkerPlan(
        organizationId,
        selectedWorker.worker_id,
        selectedGoal.goal_id,
        plan.plan_id,
        plan.version,
        { status: plan.status === "draft" ? "ready" : "draft" },
      );
      setWorkerPlans(
        await organizationsApi.digitalWorkerPlans(
          organizationId,
          selectedWorker.worker_id,
          selectedGoal.goal_id,
        ),
      );
    } catch {
      message.error(t("organizations.operationFailed"));
    }
  }

  function openPlanRunModal(plan: DigitalWorkerPlan) {
    if (!selectedWorker) return;
    const matching = scenes.filter(
      (scene) =>
        scene.enabled &&
        scene.position_id === selectedWorker.position_id &&
        scene.agent_id === selectedWorker.agent_id,
    );
    setRunRequestPlan(plan);
    runRequestForm.resetFields();
    runRequestForm.setFieldsValue({ scene_id: matching[0]?.scene_id });
    setRunRequestOpen(true);
  }

  async function submitPlanRun(values: Record<string, unknown>) {
    if (!organizationId || !runRequestPlan) return;
    setSaving(true);
    try {
      await organizationsApi.requestSceneRun(
        organizationId,
        String(values.scene_id ?? ""),
        runRequestPlan.plan_id,
      );
      setRunRequestOpen(false);
      await load(organizationId);
      message.success(t("organizations.runSubmitted"));
    } catch {
      message.error(t("organizations.operationFailed"));
    } finally {
      setSaving(false);
    }
  }

  function openCapabilityProposal(run: EnterpriseSceneRun) {
    setCapabilityProposalRunId(run.run_id);
    capabilityProposalForm.resetFields();
    setCapabilityProposalModalOpen(true);
  }

  async function saveCapabilityProposal(values: Record<string, unknown>) {
    if (!organizationId || !selectedWorker || !capabilityProposalRunId) return;
    const toLines = (value: unknown) =>
      String(value ?? "")
        .split("\n")
        .map((item) => item.trim())
        .filter(Boolean);
    setSaving(true);
    try {
      await organizationsApi.createDigitalWorkerCapabilityProposal(
        organizationId,
        selectedWorker.worker_id,
        {
          source_run_id: capabilityProposalRunId,
          source_summary: String(values.source_summary ?? "").trim(),
          observed_gaps: String(values.observed_gaps ?? "").trim(),
          expected_benefit: String(values.expected_benefit ?? "").trim(),
          risk_notes: String(values.risk_notes ?? "").trim(),
          proposed_changes: {
            skills: toLines(values.skills),
            knowledge: toLines(values.knowledge),
            operating_guidance: toLines(values.operating_guidance),
            skill_package_ids: Array.isArray(values.skill_package_ids)
              ? values.skill_package_ids.map(String)
              : [],
            knowledge_base_ids: Array.isArray(values.knowledge_base_ids)
              ? values.knowledge_base_ids.map(String)
              : [],
          },
        },
      );
      setCapabilityProposalModalOpen(false);
      setCapabilityProposalRunId(undefined);
      setCapabilityProposals(
        await organizationsApi.digitalWorkerCapabilityProposals(
          organizationId,
          selectedWorker.worker_id,
        ),
      );
      message.success(t("organizations.capabilityProposalSubmitted"));
    } catch {
      message.error(t("organizations.operationFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function reviewCapabilityProposal(
    proposal: DigitalWorkerCapabilityProposal,
    approve: boolean,
  ) {
    if (!organizationId || !selectedWorker) return;
    try {
      await organizationsApi.reviewDigitalWorkerCapabilityProposal(
        organizationId,
        selectedWorker.worker_id,
        proposal.proposal_id,
        approve,
        approve ? t("organizations.capabilityProposalApproveNote") : "",
      );
      const [proposals, workers] = await Promise.all([
        organizationsApi.digitalWorkerCapabilityProposals(
          organizationId,
          selectedWorker.worker_id,
        ),
        organizationsApi.digitalWorkers(organizationId),
      ]);
      setCapabilityProposals(proposals);
      setDigitalWorkers(workers);
      setSelectedWorker(
        workers.find((item) => item.worker_id === selectedWorker.worker_id),
      );
      message.success(
        t(
          approve
            ? "organizations.capabilityProposalPublished"
            : "organizations.capabilityProposalRejected",
        ),
      );
    } catch {
      message.error(t("organizations.operationFailed"));
    }
  }

  function openSceneModal(scene?: EnterpriseScene) {
    setEditingScene(scene);
    sceneForm.resetFields();
    sceneForm.setFieldsValue(
      scene
        ? {
            name: scene.name,
            position_id: scene.position_id,
            agent_id: scene.agent_id,
            prompt: scene.prompt,
            mcp_servers: scene.mcp_servers.join(", "),
          }
        : { mcp_servers: "" },
    );
    setSceneModalOpen(true);
  }

  async function saveScene(values: Record<string, unknown>) {
    if (!organizationId) return;
    setSaving(true);
    const body = {
      name: String(values.name ?? "").trim(),
      position_id: String(values.position_id ?? ""),
      agent_id: String(values.agent_id ?? ""),
      prompt: String(values.prompt ?? "").trim(),
      mcp_servers: String(values.mcp_servers ?? "")
        .split(",")
        .map((name) => name.trim())
        .filter(Boolean),
    };
    try {
      if (editingScene)
        await organizationsApi.updateScene(
          organizationId,
          editingScene.scene_id,
          editingScene.version,
          {
            name: body.name,
            prompt: body.prompt,
            mcp_servers: body.mcp_servers,
          },
        );
      else await organizationsApi.createScene(organizationId, body);
      setSceneModalOpen(false);
      await load(organizationId);
      message.success(t("organizations.saved"));
    } catch {
      message.error(t("organizations.operationFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function showSceneVersions(scene: EnterpriseScene) {
    if (!organizationId) return;
    try {
      setSceneVersions(
        await organizationsApi.sceneVersions(organizationId, scene.scene_id),
      );
      setVersionsModalScene(scene);
    } catch {
      message.error(t("organizations.operationFailed"));
    }
  }

  async function requestSceneRun(scene: EnterpriseScene) {
    if (!organizationId) return;
    try {
      await organizationsApi.requestSceneRun(organizationId, scene.scene_id);
      await load(organizationId);
      message.success(t("organizations.runSubmitted"));
    } catch {
      message.error(t("organizations.operationFailed"));
    }
  }

  async function decideSceneRun(
    run: EnterpriseSceneRun,
    decision: "approve" | "reject",
  ) {
    if (!organizationId) return;
    try {
      await organizationsApi.decideSceneRun(
        organizationId,
        run.run_id,
        decision,
      );
      await load(organizationId);
      message.success(
        t(
          decision === "approve"
            ? "organizations.runApproved"
            : "organizations.runRejected",
        ),
      );
    } catch {
      message.error(t("organizations.operationFailed"));
    }
  }

  async function loadSceneEvidence(run: EnterpriseSceneRun) {
    if (!organizationId) return;
    setLoadingEvidence(run.run_id);
    try {
      const result = await organizationsApi.sceneRunEvidence(
        organizationId,
        run.run_id,
      );
      setSceneEvidence((current) => ({
        ...current,
        [run.run_id]: result.available ? result.answer : null,
      }));
    } catch {
      message.error(t("organizations.evidenceLoadFailed"));
    } finally {
      setLoadingEvidence(undefined);
    }
  }

  function openModal(
    kind: ModalKind,
    initial?: Record<string, unknown>,
    department?: OrganizationDepartment,
    position?: OrganizationPosition,
  ) {
    setModalKind(kind);
    setEditingDepartment(department);
    setEditingPosition(position);
    setAssignPositionId(
      typeof initial?.position_id === "string"
        ? initial.position_id
        : undefined,
    );
    form.resetFields();
    form.setFieldsValue(initial ?? {});
  }
  function closeModal() {
    setModalKind(undefined);
    setEditingDepartment(undefined);
    setEditingPosition(undefined);
    setAssignPositionId(undefined);
    form.resetFields();
  }

  async function submit(values: Record<string, unknown>) {
    if (!organizationId && modalKind !== "organization") return;
    setSaving(true);
    try {
      if (modalKind === "organization") {
        if (chart && organizationId) {
          await organizationsApi.update(organizationId, {
            name: String(values.name ?? ""),
            description: String(values.description ?? ""),
          });
          await load(organizationId);
        } else {
          const created = await organizationsApi.create({
            name: String(values.name ?? ""),
            description: String(values.description ?? ""),
          });
          await load(created.organization_id);
        }
      } else if (modalKind === "department" && organizationId) {
        const body = {
          name: String(values.name ?? ""),
          code: String(values.code ?? ""),
          description: String(values.description ?? ""),
          parent_department_id:
            (values.parent_department_id as string | undefined) || null,
        };
        if (editingDepartment) {
          await organizationsApi.updateDepartment(
            organizationId,
            editingDepartment.department_id,
            body,
          );
        } else await organizationsApi.createDepartment(organizationId, body);
        await load(organizationId);
      } else if (modalKind === "position" && organizationId) {
        const body = {
          title: String(values.title ?? ""),
          code: String(values.code ?? ""),
          department_id: (values.department_id as string | undefined) || null,
          reports_to_position_id:
            (values.reports_to_position_id as string | undefined) || null,
          duties: String(values.duties ?? ""),
          capabilities: String(values.capabilities ?? "")
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
        };
        if (editingPosition) {
          await organizationsApi.updatePosition(
            organizationId,
            editingPosition.position_id,
            body,
          );
        } else await organizationsApi.createPosition(organizationId, body);
        await load(organizationId);
      } else if (modalKind === "member" && organizationId) {
        await organizationsApi.addMember(
          organizationId,
          Number(values.user_id),
          values.role === "manager" ? "manager" : "member",
        );
        await load(organizationId);
      } else if (
        modalKind === "appointment" &&
        organizationId &&
        assignPositionId
      ) {
        await organizationsApi.assign(
          organizationId,
          assignPositionId,
          values.subject_type as AssignmentKind,
          String(values.subject_id ?? ""),
        );
        await load(organizationId);
      }
      message.success(t("organizations.saved"));
      closeModal();
    } catch {
      message.error(t("organizations.operationFailed"));
    } finally {
      setSaving(false);
    }
  }

  const departmentColumns: ColumnsType<OrganizationDepartment> = [
    { title: t("organizations.department"), dataIndex: "name", key: "name" },
    { title: t("organizations.code"), dataIndex: "code", key: "code" },
    {
      title: t("organizations.parentDepartment"),
      dataIndex: "parent_department_id",
      key: "parent",
      render: (id: string | null) => (id ? departmentNames.get(id) : "—"),
    },
    {
      title: t("common.actions"),
      key: "actions",
      render: (_: unknown, row) =>
        canManage ? (
          <Space>
            <Button
              aria-label={t("common.edit")}
              icon={<Pencil size={14} />}
              onClick={() =>
                openModal(
                  "department",
                  row as unknown as Record<string, unknown>,
                  row,
                )
              }
            />
            <Popconfirm
              title={t("organizations.deleteDepartmentConfirm")}
              onConfirm={async () => {
                if (!organizationId) return;
                try {
                  await organizationsApi.deleteDepartment(
                    organizationId,
                    row.department_id,
                  );
                  await load(organizationId);
                } catch {
                  message.error(t("organizations.operationFailed"));
                }
              }}
            >
              <Button
                aria-label={t("common.delete")}
                danger
                icon={<Trash2 size={14} />}
              />
            </Popconfirm>
          </Space>
        ) : null,
    },
  ];
  const positionColumns: ColumnsType<OrganizationPosition> = [
    { title: t("organizations.position"), dataIndex: "title", key: "title" },
    { title: t("organizations.code"), dataIndex: "code", key: "code" },
    {
      title: t("organizations.department"),
      dataIndex: "department_id",
      key: "department",
      render: (id: string | null) => (id ? departmentNames.get(id) : "—"),
    },
    {
      title: t("organizations.reportsTo"),
      dataIndex: "reports_to_position_id",
      key: "reportsTo",
      render: (id: string | null) => (id ? positionNames.get(id) : "—"),
    },
    {
      title: t("organizations.appointments"),
      dataIndex: "assignments",
      key: "assignments",
      render: (items: OrganizationPosition["assignments"]) =>
        items.length
          ? items.map((item) =>
              canManage ? (
                <Tag
                  key={item.assignment_id}
                  closable
                  onClose={(event) => {
                    event.preventDefault();
                    if (!organizationId) return;
                    void organizationsApi
                      .unassign(organizationId, item.assignment_id)
                      .then(() => load(organizationId))
                      .catch(() =>
                        message.error(t("organizations.operationFailed")),
                      );
                  }}
                >
                  {item.label}
                </Tag>
              ) : (
                <Tag key={item.assignment_id}>{item.label}</Tag>
              ),
            )
          : "—",
    },
    {
      title: t("common.actions"),
      key: "actions",
      render: (_: unknown, row) =>
        canManage ? (
          <Space>
            <Button
              aria-label={t("common.edit")}
              icon={<Pencil size={14} />}
              onClick={() =>
                openModal(
                  "position",
                  {
                    ...row,
                    capabilities: row.capabilities.join(", "),
                  } as unknown as Record<string, unknown>,
                  undefined,
                  row,
                )
              }
            />
            <Button
              aria-label={t("organizations.assign")}
              icon={<UserRoundPlus size={14} />}
              onClick={() =>
                openModal("appointment", { position_id: row.position_id })
              }
            />
            <Popconfirm
              title={t("organizations.deletePositionConfirm")}
              onConfirm={async () => {
                if (!organizationId) return;
                try {
                  await organizationsApi.deletePosition(
                    organizationId,
                    row.position_id,
                  );
                  await load(organizationId);
                } catch {
                  message.error(t("organizations.operationFailed"));
                }
              }}
            >
              <Button
                aria-label={t("common.delete")}
                danger
                icon={<Trash2 size={14} />}
              />
            </Popconfirm>
          </Space>
        ) : null,
    },
  ];
  const memberColumns = [
    {
      title: t("organizations.person"),
      key: "name",
      render: (_: unknown, row: OrganizationChart["members"][number]) =>
        row.display_name || row.username,
    },
    { title: t("organizations.userId"), dataIndex: "user_id", key: "user_id" },
    {
      title: t("organizations.memberRole"),
      dataIndex: "role",
      key: "role",
      render: (role: string) => t("organizations.roles." + role),
    },
    {
      title: t("common.actions"),
      key: "actions",
      render: (_: unknown, row: OrganizationChart["members"][number]) =>
        canManageMembers && row.role !== "owner" ? (
          <Popconfirm
            title={t("organizations.removeMemberConfirm")}
            onConfirm={async () => {
              if (!organizationId) return;
              try {
                await organizationsApi.removeMember(
                  organizationId,
                  row.user_id,
                );
                await load(organizationId);
              } catch {
                message.error(t("organizations.operationFailed"));
              }
            }}
          >
            <Button
              aria-label={t("organizations.removeMember")}
              danger
              icon={<UserRoundX size={14} />}
            />
          </Popconfirm>
        ) : null,
    },
  ];
  const workerColumns: ColumnsType<DigitalWorker> = [
    { title: t("organizations.workerName"), dataIndex: "name", key: "name" },
    {
      title: t("organizations.position"),
      dataIndex: "position_title",
      key: "position",
    },
    { title: t("organizations.agent"), dataIndex: "agent_name", key: "agent" },
    {
      title: t("organizations.workerStatus"),
      dataIndex: "status",
      key: "status",
      render: (status: DigitalWorker["status"]) => (
        <Tag color={status === "active" ? "green" : "default"}>
          {t("organizations.workerStatuses." + status)}
        </Tag>
      ),
    },
    {
      title: t("organizations.duties"),
      dataIndex: "responsibilities",
      key: "responsibilities",
      ellipsis: true,
    },
    {
      title: t("common.actions"),
      key: "actions",
      render: (_: unknown, worker) => (
        <Space>
          <Button
            icon={<Target size={14} />}
            onClick={() => {
              setSelectedGoal(undefined);
              setWorkerPlans([]);
              setSelectedWorker(worker);
            }}
          >
            {t("organizations.manageGoals")}
          </Button>
          {canManage && worker.status !== "archived" && (
            <Button onClick={() => void changeWorkerStatus(worker)}>
              {t(
                worker.status === "active"
                  ? "organizations.pauseWorker"
                  : "organizations.activateWorker",
              )}
            </Button>
          )}
        </Space>
      ),
    },
  ];
  const goalColumns: ColumnsType<DigitalWorkerGoal> = [
    { title: t("organizations.goalTitle"), dataIndex: "title", key: "title" },
    {
      title: t("organizations.goalPeriod"),
      key: "period",
      render: (_: unknown, goal) =>
        [goal.period_start, goal.period_end].filter(Boolean).join(" — ") || "—",
    },
    {
      title: t("organizations.successCriteria"),
      dataIndex: "success_criteria",
      key: "criteria",
      ellipsis: true,
    },
    {
      title: t("organizations.goalStatus"),
      dataIndex: "status",
      key: "status",
      render: (status: DigitalWorkerGoal["status"]) => (
        <Tag color={status === "completed" ? "green" : "blue"}>
          {t("organizations.goalStatuses." + status)}
        </Tag>
      ),
    },
    {
      title: t("common.actions"),
      key: "actions",
      render: (_: unknown, goal) => (
        <Space>
          <Button
            icon={<ClipboardList size={14} />}
            onClick={() => setSelectedGoal(goal)}
          >
            {t("organizations.managePlans")}
          </Button>
          {canManage &&
            (goal.status === "draft" || goal.status === "active") && (
              <Button onClick={() => void changeGoalStatus(goal)}>
                {t(
                  goal.status === "draft"
                    ? "organizations.activateGoal"
                    : "organizations.completeGoal",
                )}
              </Button>
            )}
        </Space>
      ),
    },
  ];
  const planColumns: ColumnsType<DigitalWorkerPlan> = [
    { title: t("organizations.planTitle"), dataIndex: "title", key: "title" },
    {
      title: t("organizations.planSteps"),
      key: "steps",
      render: (_: unknown, plan) => plan.steps.length,
    },
    {
      title: t("organizations.planStatus"),
      dataIndex: "status",
      key: "status",
      render: (status: DigitalWorkerPlan["status"]) => (
        <Tag color={status === "ready" ? "green" : "default"}>
          {t("organizations.planStatuses." + status)}
        </Tag>
      ),
    },
    {
      title: t("common.actions"),
      key: "actions",
      render: (_: unknown, plan) =>
        plan.status !== "cancelled" ? (
          <Space>
            {canManage && plan.status === "draft" && (
              <Button onClick={() => openPlanModal(plan)}>
                {t("common.edit")}
              </Button>
            )}
            {canManage && (
              <Button onClick={() => void changePlanStatus(plan)}>
                {t(
                  plan.status === "draft"
                    ? "organizations.preparePlan"
                    : "organizations.reopenPlan",
                )}
              </Button>
            )}
            {plan.status === "ready" && (
              <Button
                type="primary"
                icon={<Play size={14} />}
                onClick={() => openPlanRunModal(plan)}
              >
                {t("organizations.requestRun")}
              </Button>
            )}
          </Space>
        ) : null,
    },
  ];
  const capabilityProposalColumns: ColumnsType<DigitalWorkerCapabilityProposal> =
    [
      {
        title: t("organizations.capabilityProposalEvidence"),
        dataIndex: "source_run_id",
        key: "source_run_id",
        render: (runId: string) => runId.slice(0, 10),
      },
      {
        title: t("organizations.capabilityVersion"),
        dataIndex: "base_capability_version",
        key: "base_capability_version",
        render: (version: number) => `v${version}`,
      },
      {
        title: t("organizations.capabilityProposalStatus"),
        dataIndex: "status",
        key: "status",
        render: (status: DigitalWorkerCapabilityProposal["status"]) => (
          <Tag
            color={
              status === "published"
                ? "green"
                : status === "rejected"
                ? "red"
                : "blue"
            }
          >
            {t(`organizations.capabilityProposalStatuses.${status}`)}
          </Tag>
        ),
      },
      {
        title: t("common.actions"),
        key: "actions",
        render: (_: unknown, proposal) =>
          canManage &&
          proposal.status === "submitted" &&
          Number(currentUser?.id) !== proposal.created_by ? (
            <Space>
              <Popconfirm
                title={t("organizations.capabilityProposalReviewConfirm")}
                onConfirm={() => void reviewCapabilityProposal(proposal, true)}
              >
                <Button type="primary" size="small">
                  {t("organizations.capabilityProposalPublish")}
                </Button>
              </Popconfirm>
              <Popconfirm
                title={t("organizations.capabilityProposalReviewConfirm")}
                onConfirm={() => void reviewCapabilityProposal(proposal, false)}
              >
                <Button danger size="small">
                  {t("organizations.capabilityProposalReject")}
                </Button>
              </Popconfirm>
            </Space>
          ) : null,
      },
    ];
  const sceneColumns: ColumnsType<EnterpriseScene> = [
    { title: t("organizations.sceneName"), dataIndex: "name", key: "name" },
    {
      title: t("organizations.sceneVersion"),
      dataIndex: "version",
      key: "version",
      render: (version: number) => `v${version}`,
    },
    {
      title: t("organizations.position"),
      dataIndex: "position_id",
      key: "position",
      render: (id: string) => positionNames.get(id) ?? id,
    },
    {
      title: t("organizations.agent"),
      dataIndex: "agent_id",
      key: "agent",
      render: (id: string) =>
        chart?.positions
          .flatMap((position) => position.assignments)
          .find(
            (item) => item.subject_type === "agent" && item.subject_id === id,
          )?.label ?? id,
    },
    {
      title: t("organizations.sceneEnabled"),
      dataIndex: "enabled",
      key: "enabled",
      render: (enabled: number | boolean) => (
        <Tag color={enabled ? "green" : "default"}>
          {t(enabled ? "organizations.enabled" : "organizations.disabled")}
        </Tag>
      ),
    },
    {
      title: t("common.actions"),
      key: "actions",
      render: (_: unknown, scene) => (
        <Space>
          <Button onClick={() => void showSceneVersions(scene)}>
            {t("organizations.versionHistory")}
          </Button>
          <Button
            icon={<Play size={14} />}
            disabled={!scene.enabled}
            onClick={() => void requestSceneRun(scene)}
          >
            {t("organizations.requestRun")}
          </Button>
          {canManage && (
            <Button
              icon={<Pencil size={14} />}
              onClick={() => openSceneModal(scene)}
            >
              {t("common.edit")}
            </Button>
          )}
          {canManage && (
            <Button
              onClick={async () => {
                if (!organizationId) return;
                try {
                  await organizationsApi.updateScene(
                    organizationId,
                    scene.scene_id,
                    scene.version,
                    { enabled: !scene.enabled },
                  );
                  await load(organizationId);
                } catch {
                  message.error(t("organizations.operationFailed"));
                }
              }}
            >
              {t(
                scene.enabled
                  ? "organizations.disable"
                  : "organizations.enable",
              )}
            </Button>
          )}
        </Space>
      ),
    },
  ];
  const runColumns: ColumnsType<EnterpriseSceneRun> = [
    {
      title: t("organizations.sceneName"),
      dataIndex: "scene_name",
      key: "scene",
    },
    {
      title: t("organizations.planSnapshot"),
      key: "plan",
      render: (_: unknown, run) => run.plan_snapshot?.plan.title ?? "—",
    },
    {
      title: t("organizations.runStatus"),
      dataIndex: "status",
      key: "status",
      render: (status: EnterpriseSceneRun["status"]) => (
        <Tag
          color={
            status === "succeeded"
              ? "green"
              : status === "failed" || status === "rejected"
              ? "red"
              : "blue"
          }
        >
          {t("organizations.runStatuses." + status)}
        </Tag>
      ),
    },
    {
      title: t("organizations.requestedBy"),
      dataIndex: "requested_by",
      key: "requester",
    },
    {
      title: t("organizations.approvedBy"),
      dataIndex: "approved_by",
      key: "approver",
      render: (id: number | null) => id ?? "—",
    },
    {
      title: t("organizations.createdAt"),
      dataIndex: "created_at",
      key: "created",
      render: (ts: number) => formatServerDateTime(ts, timezone),
    },
    {
      title: t("common.actions"),
      key: "actions",
      render: (_: unknown, run) =>
        canManage && run.status === "awaiting_approval" ? (
          <Space>
            <Button
              type="primary"
              icon={<Check size={14} />}
              onClick={() => void decideSceneRun(run, "approve")}
            >
              {t("organizations.approve")}
            </Button>
            <Button
              danger
              icon={<X size={14} />}
              onClick={() => void decideSceneRun(run, "reject")}
            >
              {t("organizations.reject")}
            </Button>
          </Space>
        ) : null,
    },
  ];

  const modalTitle =
    modalKind === "organization"
      ? chart
        ? t("organizations.editOrganization")
        : t("organizations.createOrganization")
      : modalKind === "department"
      ? editingDepartment
        ? t("organizations.editDepartment")
        : t("organizations.addDepartment")
      : modalKind === "position"
      ? editingPosition
        ? t("organizations.editPosition")
        : t("organizations.addPosition")
      : modalKind === "member"
      ? t("organizations.addMember")
      : t("organizations.assign");

  return (
    <div style={{ padding: 24, maxWidth: 1400, margin: "0 auto" }}>
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <div>
          <Typography.Title level={2}>
            <Building2
              size={24}
              style={{ marginRight: 8, verticalAlign: "middle" }}
            />
            {t("organizations.title")}
          </Typography.Title>
          <Typography.Paragraph type="secondary">
            {t("organizations.description")}
          </Typography.Paragraph>
        </div>
        <Card>
          <Space
            wrap
            style={{ width: "100%", justifyContent: "space-between" }}
          >
            <Select
              aria-label={t("organizations.selectOrganization")}
              style={{ minWidth: 280 }}
              value={organizationId}
              placeholder={t("organizations.selectOrganization")}
              options={organizations.map((item) => ({
                value: item.organization_id,
                label: item.name,
              }))}
              onChange={(value) => {
                setOrganizationId(value);
                setSelectedWorker(undefined);
                setSelectedGoal(undefined);
                setWorkerPlans([]);
                void load(value);
              }}
            />
            <Space wrap>
              {chart && canManageMembers && (
                <Button
                  icon={<Pencil size={14} />}
                  onClick={() =>
                    openModal(
                      "organization",
                      chart.organization as unknown as Record<string, unknown>,
                    )
                  }
                >
                  {t("organizations.editOrganization")}
                </Button>
              )}
              {canManage && (
                <>
                  <Button
                    icon={<Plus size={14} />}
                    onClick={() => openModal("department")}
                  >
                    {t("organizations.addDepartment")}
                  </Button>
                  <Button
                    type="primary"
                    icon={<Plus size={14} />}
                    onClick={() => openModal("position")}
                  >
                    {t("organizations.addPosition")}
                  </Button>
                </>
              )}
              {canManageMembers && (
                <Button
                  icon={<UserRoundPlus size={14} />}
                  onClick={() => openModal("member")}
                >
                  {t("organizations.addMember")}
                </Button>
              )}
              {chart && canManageMembers && (
                <Popconfirm
                  title={t("organizations.deleteOrganizationConfirm")}
                  onConfirm={async () => {
                    try {
                      await organizationsApi.remove(
                        chart.organization.organization_id,
                      );
                      setChart(undefined);
                      setOrganizationId(undefined);
                      await load();
                    } catch {
                      message.error(t("organizations.operationFailed"));
                    }
                  }}
                >
                  <Button danger icon={<Trash2 size={14} />}>
                    {t("common.delete")}
                  </Button>
                </Popconfirm>
              )}
              {!chart && (
                <Button
                  type="primary"
                  icon={<Plus size={14} />}
                  onClick={() => openModal("organization")}
                >
                  {t("organizations.createOrganization")}
                </Button>
              )}
            </Space>
          </Space>
        </Card>

        {loading ? (
          <Spin />
        ) : !chart ? (
          <Card>
            <Empty description={t("organizations.empty")}>
              <Button type="primary" onClick={() => openModal("organization")}>
                {t("organizations.createOrganization")}
              </Button>
            </Empty>
          </Card>
        ) : (
          <Tabs
            items={[
              {
                key: "chart",
                label: t("organizations.organizationChart"),
                children: (
                  <Space
                    direction="vertical"
                    size="large"
                    style={{ width: "100%" }}
                  >
                    <Card title={t("organizations.departments")}>
                      <Table
                        rowKey="department_id"
                        size="small"
                        pagination={false}
                        dataSource={chart.departments}
                        columns={departmentColumns}
                      />
                    </Card>
                    <Card title={t("organizations.positions")}>
                      <Table
                        rowKey="position_id"
                        size="small"
                        pagination={false}
                        dataSource={chart.positions}
                        columns={positionColumns}
                      />
                    </Card>
                  </Space>
                ),
              },
              {
                key: "members",
                label: t("organizations.members"),
                children: (
                  <Card>
                    <Table
                      rowKey="user_id"
                      size="small"
                      pagination={false}
                      dataSource={chart.members}
                      columns={memberColumns}
                    />
                  </Card>
                ),
              },
              {
                key: "digital-workers",
                label: t("organizations.virtualWorkers"),
                children: (
                  <Space
                    direction="vertical"
                    size="large"
                    style={{ width: "100%" }}
                  >
                    <Card
                      title={t("organizations.virtualWorkers")}
                      extra={
                        canManage ? (
                          <Button
                            type="primary"
                            icon={<BriefcaseBusiness size={14} />}
                            onClick={openWorkerModal}
                          >
                            {t("organizations.createWorker")}
                          </Button>
                        ) : null
                      }
                    >
                      <Typography.Paragraph type="secondary">
                        {t("organizations.workerGovernanceHint")}
                      </Typography.Paragraph>
                      <Table
                        rowKey="worker_id"
                        size="small"
                        pagination={{ pageSize: 8 }}
                        dataSource={digitalWorkers}
                        columns={workerColumns}
                      />
                    </Card>
                    {selectedWorker && (
                      <Card
                        title={`${t("organizations.stageGoals")} · ${
                          selectedWorker.name
                        }`}
                        extra={
                          <Space>
                            <Button
                              onClick={() => {
                                setSelectedWorker(undefined);
                                setSelectedGoal(undefined);
                                setWorkerPlans([]);
                              }}
                            >
                              {t("common.close")}
                            </Button>
                            {canManage && (
                              <Button
                                type="primary"
                                icon={<Plus size={14} />}
                                onClick={() => {
                                  goalForm.resetFields();
                                  setGoalModalOpen(true);
                                }}
                              >
                                {t("organizations.addGoal")}
                              </Button>
                            )}
                          </Space>
                        }
                      >
                        <Typography.Paragraph type="secondary">
                          {selectedWorker.responsibilities || "—"}
                        </Typography.Paragraph>
                        <Table
                          rowKey="goal_id"
                          size="small"
                          pagination={false}
                          dataSource={workerGoals}
                          columns={goalColumns}
                        />
                      </Card>
                    )}
                    {selectedWorker && (
                      <Card
                        title={`${t("organizations.capabilityProfile")} · ${
                          selectedWorker.name
                        }`}
                        extra={
                          canManage && selectedWorker.status === "active" ? (
                            <Space>
                              <Select
                                style={{ minWidth: 260 }}
                                placeholder={t(
                                  "organizations.capabilityChooseEvidence",
                                )}
                                value={capabilityProposalRunId}
                                onChange={setCapabilityProposalRunId}
                                options={sceneRuns
                                  .filter(
                                    (run) =>
                                      run.status === "succeeded" &&
                                      run.plan_snapshot?.worker.worker_id ===
                                        selectedWorker.worker_id &&
                                      !capabilityProposals.some(
                                        (proposal) =>
                                          proposal.source_run_id === run.run_id,
                                      ),
                                  )
                                  .map((run) => ({
                                    value: run.run_id,
                                    label: `${
                                      run.scene_name
                                    } · ${formatServerDateTime(
                                      run.created_at,
                                      timezone,
                                    )}`,
                                  }))}
                              />
                              <Button
                                type="primary"
                                disabled={!capabilityProposalRunId}
                                onClick={() => {
                                  const run = sceneRuns.find(
                                    (item) =>
                                      item.run_id === capabilityProposalRunId,
                                  );
                                  if (run) openCapabilityProposal(run);
                                }}
                              >
                                {t("organizations.addCapabilityProposal")}
                              </Button>
                            </Space>
                          ) : null
                        }
                      >
                        <Typography.Paragraph type="secondary">
                          {t("organizations.capabilityGovernanceHint", {
                            version: selectedWorker.capability_version,
                          })}
                        </Typography.Paragraph>
                        <Space wrap style={{ marginBottom: 16 }}>
                          {selectedWorker.capabilities.skills.map((value) => (
                            <Tag key={`skill-${value}`}>{value}</Tag>
                          ))}
                          {selectedWorker.capabilities.knowledge.map(
                            (value) => (
                              <Tag key={`knowledge-${value}`} color="blue">
                                {value}
                              </Tag>
                            ),
                          )}
                          {selectedWorker.capabilities.operating_guidance.map(
                            (value) => (
                              <Tag key={`guidance-${value}`} color="purple">
                                {value}
                              </Tag>
                            ),
                          )}
                          {selectedWorker.capabilities.skill_package_ids.map(
                            (id) => (
                              <Tag key={`package-${id}`} color="cyan">
                                {authorizedWorkerResources.skills.find(
                                  (item) => item.id === id,
                                )?.name ?? id}
                              </Tag>
                            ),
                          )}
                          {selectedWorker.capabilities.knowledge_base_ids.map(
                            (id) => (
                              <Tag key={`kb-${id}`} color="geekblue">
                                {authorizedWorkerResources.knowledge_bases.find(
                                  (item) => item.id === id,
                                )?.name ?? id}
                              </Tag>
                            ),
                          )}
                          {Object.values(selectedWorker.capabilities).every(
                            (values) => values.length === 0,
                          ) && (
                            <Typography.Text type="secondary">
                              —
                            </Typography.Text>
                          )}
                        </Space>
                        <Table
                          rowKey="proposal_id"
                          size="small"
                          pagination={{ pageSize: 5 }}
                          dataSource={capabilityProposals}
                          columns={capabilityProposalColumns}
                          expandable={{
                            expandedRowRender: (proposal) => (
                              <div>
                                <Typography.Paragraph>
                                  <strong>
                                    {t(
                                      "organizations.capabilityProposalSummary",
                                    )}
                                    :
                                  </strong>{" "}
                                  {proposal.source_summary}
                                </Typography.Paragraph>
                                <Typography.Paragraph>
                                  <strong>
                                    {t("organizations.capabilityObservedGaps")}:
                                  </strong>{" "}
                                  {proposal.observed_gaps}
                                </Typography.Paragraph>
                                <Typography.Paragraph>
                                  <strong>
                                    {t(
                                      "organizations.capabilityExpectedBenefit",
                                    )}
                                    :
                                  </strong>{" "}
                                  {proposal.expected_benefit}
                                </Typography.Paragraph>
                                {proposal.risk_notes && (
                                  <Typography.Paragraph>
                                    <strong>
                                      {t("organizations.capabilityRiskNotes")}:
                                    </strong>{" "}
                                    {proposal.risk_notes}
                                  </Typography.Paragraph>
                                )}
                                {proposal.review_note && (
                                  <Typography.Paragraph>
                                    <strong>
                                      {t("organizations.capabilityReviewNote")}:
                                    </strong>{" "}
                                    {proposal.review_note}
                                  </Typography.Paragraph>
                                )}
                              </div>
                            ),
                          }}
                        />
                      </Card>
                    )}
                    {selectedWorker && selectedGoal && (
                      <Card
                        title={`${t("organizations.workPlans")} · ${
                          selectedGoal.title
                        }`}
                        extra={
                          <Space>
                            <Button onClick={() => setSelectedGoal(undefined)}>
                              {t("common.close")}
                            </Button>
                            {canManage && selectedGoal.status === "active" && (
                              <Button
                                type="primary"
                                icon={<Plus size={14} />}
                                onClick={() => openPlanModal()}
                              >
                                {t("organizations.addPlan")}
                              </Button>
                            )}
                          </Space>
                        }
                      >
                        <Typography.Paragraph type="secondary">
                          {t("organizations.planGovernanceHint")}
                        </Typography.Paragraph>
                        <Table
                          rowKey="plan_id"
                          size="small"
                          pagination={false}
                          dataSource={workerPlans}
                          columns={planColumns}
                          expandable={{
                            expandedRowRender: (plan) => (
                              <div>
                                <Typography.Paragraph>
                                  <strong>
                                    {t("organizations.planApproach")}:
                                  </strong>{" "}
                                  {plan.approach || "—"}
                                </Typography.Paragraph>
                                <ol>
                                  {plan.steps.map((step, index) => (
                                    <li key={`${plan.plan_id}-${index}`}>
                                      {step.action} — {step.expected_output}
                                    </li>
                                  ))}
                                </ol>
                              </div>
                            ),
                          }}
                        />
                      </Card>
                    )}
                  </Space>
                ),
              },
              {
                key: "scenes",
                label: t("organizations.scenes"),
                children: (
                  <Space
                    direction="vertical"
                    size="large"
                    style={{ width: "100%" }}
                  >
                    <Card
                      title={t("organizations.scenes")}
                      extra={
                        canManage ? (
                          <Button
                            type="primary"
                            icon={<Plus size={14} />}
                            onClick={() => openSceneModal()}
                          >
                            {t("organizations.createScene")}
                          </Button>
                        ) : null
                      }
                    >
                      <Table
                        rowKey="scene_id"
                        size="small"
                        pagination={{ pageSize: 8 }}
                        dataSource={scenes}
                        columns={sceneColumns}
                      />
                    </Card>
                    <Card title={t("organizations.sceneRuns")}>
                      <Table
                        rowKey="run_id"
                        size="small"
                        pagination={{ pageSize: 8 }}
                        dataSource={sceneRuns}
                        columns={runColumns}
                        expandable={{
                          expandedRowRender: (run) => (
                            <div>
                              <Typography.Paragraph>
                                <strong>
                                  {t("organizations.promptSnapshot")}:
                                </strong>
                                <br />
                                {run.prompt_snapshot}
                              </Typography.Paragraph>
                              {run.plan_snapshot && (
                                <Card
                                  size="small"
                                  title={`${t("organizations.planSnapshot")}: ${
                                    run.plan_snapshot.plan.title
                                  }`}
                                  style={{ marginBottom: 12 }}
                                >
                                  <Typography.Paragraph>
                                    <strong>
                                      {t("organizations.stageGoals")}:
                                    </strong>{" "}
                                    {run.plan_snapshot.goal.title}
                                  </Typography.Paragraph>
                                  <Typography.Paragraph>
                                    <strong>
                                      {t("organizations.capabilityVersion")}:
                                    </strong>{" "}
                                    v
                                    {
                                      run.plan_snapshot.worker
                                        .capability_version
                                    }
                                  </Typography.Paragraph>
                                  {run.plan_snapshot.capabilities && (
                                    <Typography.Paragraph type="secondary">
                                      {[
                                        ...run.plan_snapshot.capabilities
                                          .skills,
                                        ...run.plan_snapshot.capabilities
                                          .knowledge,
                                        ...run.plan_snapshot.capabilities
                                          .operating_guidance,
                                      ].join(" · ") || "—"}
                                    </Typography.Paragraph>
                                  )}
                                  <Typography.Paragraph>
                                    <strong>
                                      {t("organizations.successCriteria")}:
                                    </strong>{" "}
                                    {run.plan_snapshot.goal.success_criteria}
                                  </Typography.Paragraph>
                                  <Typography.Paragraph>
                                    <strong>
                                      {t("organizations.planApproach")}:
                                    </strong>{" "}
                                    {run.plan_snapshot.plan.approach}
                                  </Typography.Paragraph>
                                  <ol>
                                    {run.plan_snapshot.plan.steps.map(
                                      (step, index) => (
                                        <li key={`${run.run_id}-plan-${index}`}>
                                          {step.action} — {step.expected_output}
                                        </li>
                                      ),
                                    )}
                                  </ol>
                                </Card>
                              )}
                              <Typography.Paragraph>
                                <strong>
                                  {t("organizations.sceneVersion")}:
                                </strong>{" "}
                                v{run.scene_version}
                              </Typography.Paragraph>
                              <Typography.Paragraph>
                                <strong>
                                  {t("organizations.mcpServers")}:
                                </strong>{" "}
                                {run.mcp_servers.join(", ") || "—"}
                              </Typography.Paragraph>
                              {run.decision_note && (
                                <Typography.Paragraph>
                                  <strong>
                                    {t("organizations.decisionNote")}:
                                  </strong>{" "}
                                  {run.decision_note}
                                </Typography.Paragraph>
                              )}
                              {run.error_summary && (
                                <Typography.Paragraph type="danger">
                                  {run.error_summary}
                                </Typography.Paragraph>
                              )}
                              {run.status === "succeeded" &&
                                (currentUser?.role === "admin" ||
                                  currentUser?.id === run.requested_by ||
                                  currentUser?.id === run.approved_by) && (
                                  <>
                                    <Button
                                      size="small"
                                      loading={loadingEvidence === run.run_id}
                                      onClick={() =>
                                        void loadSceneEvidence(run)
                                      }
                                    >
                                      {t("organizations.viewEvidence")}
                                    </Button>
                                    {sceneEvidence[run.run_id] !==
                                      undefined && (
                                      <Typography.Paragraph
                                        style={{ whiteSpace: "pre-wrap" }}
                                      >
                                        {sceneEvidence[run.run_id] ??
                                          t(
                                            "organizations.evidenceUnavailable",
                                          )}
                                      </Typography.Paragraph>
                                    )}
                                  </>
                                )}
                            </div>
                          ),
                        }}
                      />
                    </Card>
                  </Space>
                ),
              },
            ]}
          />
        )}
      </Space>

      <Modal
        open={Boolean(modalKind)}
        title={modalTitle}
        onCancel={closeModal}
        onOk={() => form.submit()}
        confirmLoading={saving}
        destroyOnClose
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(values) => void submit(values)}
        >
          {modalKind === "organization" && (
            <>
              <Form.Item
                name="name"
                label={t("organizations.name")}
                rules={[{ required: true }]}
              >
                <Input maxLength={120} />
              </Form.Item>
              <Form.Item
                name="description"
                label={t("organizations.descriptionField")}
              >
                <Input.TextArea rows={3} maxLength={2000} />
              </Form.Item>
            </>
          )}
          {modalKind === "department" && (
            <>
              <Form.Item
                name="name"
                label={t("organizations.department")}
                rules={[{ required: true }]}
              >
                <Input maxLength={120} />
              </Form.Item>
              <Form.Item name="code" label={t("organizations.code")}>
                <Input maxLength={80} />
              </Form.Item>
              <Form.Item
                name="description"
                label={t("organizations.descriptionField")}
              >
                <Input.TextArea rows={2} />
              </Form.Item>
              <Form.Item
                name="parent_department_id"
                label={t("organizations.parentDepartment")}
              >
                <Select
                  allowClear
                  options={
                    chart?.departments
                      .filter(
                        (item) =>
                          item.department_id !==
                          editingDepartment?.department_id,
                      )
                      .map((item) => ({
                        value: item.department_id,
                        label: item.name,
                      })) ?? []
                  }
                />
              </Form.Item>
            </>
          )}
          {modalKind === "position" && (
            <>
              <Form.Item
                name="title"
                label={t("organizations.position")}
                rules={[{ required: true }]}
              >
                <Input maxLength={120} />
              </Form.Item>
              <Form.Item name="code" label={t("organizations.code")}>
                <Input maxLength={80} />
              </Form.Item>
              <Form.Item
                name="department_id"
                label={t("organizations.department")}
              >
                <Select
                  allowClear
                  options={
                    chart?.departments.map((item) => ({
                      value: item.department_id,
                      label: item.name,
                    })) ?? []
                  }
                />
              </Form.Item>
              <Form.Item
                name="reports_to_position_id"
                label={t("organizations.reportsTo")}
              >
                <Select
                  allowClear
                  options={
                    chart?.positions
                      .filter(
                        (item) =>
                          item.position_id !== editingPosition?.position_id,
                      )
                      .map((item) => ({
                        value: item.position_id,
                        label: item.title,
                      })) ?? []
                  }
                />
              </Form.Item>
              <Form.Item name="duties" label={t("organizations.duties")}>
                <Input.TextArea rows={3} maxLength={8000} />
              </Form.Item>
              <Form.Item
                name="capabilities"
                label={t("organizations.capabilities")}
              >
                <Input placeholder={t("organizations.capabilitiesHint")} />
              </Form.Item>
            </>
          )}
          {modalKind === "member" && (
            <>
              <Form.Item
                name="user_id"
                label={t("organizations.userId")}
                rules={[{ required: true }]}
              >
                <Input type="number" min={1} />
              </Form.Item>
              <Form.Item
                name="role"
                label={t("organizations.memberRole")}
                initialValue="member"
              >
                <Select
                  options={[
                    { value: "member", label: t("organizations.roles.member") },
                    {
                      value: "manager",
                      label: t("organizations.roles.manager"),
                    },
                  ]}
                />
              </Form.Item>
            </>
          )}
          {modalKind === "appointment" && (
            <>
              <Typography.Paragraph type="secondary">
                {positionNames.get(assignPositionId ?? "")}
              </Typography.Paragraph>
              <Form.Item
                name="subject_type"
                label={t("organizations.appointmentType")}
                initialValue="user"
              >
                <Select
                  options={[
                    { value: "user", label: t("organizations.human") },
                    { value: "agent", label: t("organizations.agent") },
                  ]}
                />
              </Form.Item>
              <Form.Item
                name="subject_id"
                label={t("organizations.assigneeId")}
                rules={[{ required: true }]}
              >
                <Input placeholder={t("organizations.assigneeIdHint")} />
              </Form.Item>
            </>
          )}
        </Form>
      </Modal>
      <Modal
        open={workerModalOpen}
        title={t("organizations.createWorker")}
        onCancel={() => setWorkerModalOpen(false)}
        onOk={() => workerForm.submit()}
        confirmLoading={saving}
        destroyOnClose
      >
        <Form
          form={workerForm}
          layout="vertical"
          onFinish={(values) => void saveWorker(values)}
        >
          <Form.Item
            name="name"
            label={t("organizations.workerName")}
            rules={[{ required: true }]}
          >
            <Input maxLength={120} />
          </Form.Item>
          <Form.Item
            name="position_id"
            label={t("organizations.position")}
            rules={[{ required: true }]}
          >
            <Select
              options={chart?.positions.map((position) => ({
                value: position.position_id,
                label: position.title,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="agent_id"
            label={t("organizations.agent")}
            rules={[{ required: true }]}
          >
            <Select options={workerAgents} />
          </Form.Item>
          <Form.Item
            name="responsibilities"
            label={t("organizations.duties")}
            extra={t("organizations.workerResponsibilitiesHint")}
          >
            <Input.TextArea rows={3} maxLength={8000} />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        open={goalModalOpen}
        title={t("organizations.addGoal")}
        onCancel={() => setGoalModalOpen(false)}
        onOk={() => goalForm.submit()}
        confirmLoading={saving}
        destroyOnClose
      >
        <Form
          form={goalForm}
          layout="vertical"
          onFinish={(values) => void saveGoal(values)}
        >
          <Form.Item
            name="title"
            label={t("organizations.goalTitle")}
            rules={[{ required: true }]}
          >
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item
            name="success_criteria"
            label={t("organizations.successCriteria")}
            rules={[{ required: true }]}
          >
            <Input.TextArea rows={3} maxLength={4000} />
          </Form.Item>
          <Form.Item
            name="description"
            label={t("organizations.descriptionField")}
          >
            <Input.TextArea rows={2} maxLength={4000} />
          </Form.Item>
          <Space style={{ width: "100%" }} align="start">
            <Form.Item name="period_start" label={t("organizations.goalStart")}>
              <Input type="date" />
            </Form.Item>
            <Form.Item name="period_end" label={t("organizations.goalEnd")}>
              <Input type="date" />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
      <Modal
        open={planModalOpen}
        title={t(
          editingPlan ? "organizations.editPlan" : "organizations.addPlan",
        )}
        onCancel={() => setPlanModalOpen(false)}
        onOk={() => planForm.submit()}
        confirmLoading={saving}
        destroyOnClose
        width={680}
      >
        <Form
          form={planForm}
          layout="vertical"
          onFinish={(values) => void savePlan(values)}
        >
          <Form.Item
            name="title"
            label={t("organizations.planTitle")}
            rules={[{ required: true }]}
          >
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item
            name="approach"
            label={t("organizations.planApproach")}
            rules={[{ required: true }]}
          >
            <Input.TextArea rows={3} maxLength={4000} />
          </Form.Item>
          <Form.List name="steps">
            {(fields, { add, remove }) => (
              <Space direction="vertical" style={{ width: "100%" }}>
                {fields.map(({ key, name, ...restField }, index) => (
                  <Card
                    key={key}
                    size="small"
                    title={`${t("organizations.planStep")} ${index + 1}`}
                    extra={
                      fields.length > 1 ? (
                        <Button danger onClick={() => remove(name)}>
                          {t("common.delete")}
                        </Button>
                      ) : null
                    }
                  >
                    <Form.Item
                      {...restField}
                      name={[name, "action"]}
                      label={t("organizations.planAction")}
                      rules={[{ required: true }]}
                    >
                      <Input maxLength={500} />
                    </Form.Item>
                    <Form.Item
                      {...restField}
                      name={[name, "expected_output"]}
                      label={t("organizations.planExpectedOutput")}
                      rules={[{ required: true }]}
                    >
                      <Input.TextArea rows={2} maxLength={1000} />
                    </Form.Item>
                  </Card>
                ))}
                <Button
                  type="dashed"
                  icon={<Plus size={14} />}
                  onClick={() => add({ action: "", expected_output: "" })}
                  block
                >
                  {t("organizations.addPlanStep")}
                </Button>
              </Space>
            )}
          </Form.List>
        </Form>
      </Modal>
      <Modal
        open={runRequestOpen}
        title={t("organizations.requestPlanRun")}
        onCancel={() => setRunRequestOpen(false)}
        onOk={() => runRequestForm.submit()}
        confirmLoading={saving}
        destroyOnClose
      >
        <Typography.Paragraph type="secondary">
          {t("organizations.planRunApprovalHint")}
        </Typography.Paragraph>
        <Form
          form={runRequestForm}
          layout="vertical"
          onFinish={(values) => void submitPlanRun(values)}
        >
          <Form.Item
            name="scene_id"
            label={t("organizations.scenes")}
            rules={[{ required: true }]}
          >
            <Select
              options={
                selectedWorker
                  ? scenes
                      .filter(
                        (scene) =>
                          scene.enabled &&
                          scene.position_id === selectedWorker.position_id &&
                          scene.agent_id === selectedWorker.agent_id,
                      )
                      .map((scene) => ({
                        value: scene.scene_id,
                        label: scene.name,
                      }))
                  : []
              }
            />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        open={capabilityProposalModalOpen}
        title={t("organizations.addCapabilityProposal")}
        onCancel={() => setCapabilityProposalModalOpen(false)}
        onOk={() => capabilityProposalForm.submit()}
        confirmLoading={saving}
        destroyOnClose
        width={760}
      >
        <Typography.Paragraph type="secondary">
          {t("organizations.capabilityProposalFormHint")}
        </Typography.Paragraph>
        <Form
          form={capabilityProposalForm}
          layout="vertical"
          onFinish={(values) => void saveCapabilityProposal(values)}
        >
          <Form.Item
            name="source_summary"
            label={t("organizations.capabilityProposalSummary")}
            rules={[{ required: true }]}
          >
            <Input.TextArea rows={2} maxLength={4000} />
          </Form.Item>
          <Form.Item
            name="observed_gaps"
            label={t("organizations.capabilityObservedGaps")}
            rules={[{ required: true }]}
          >
            <Input.TextArea rows={2} maxLength={4000} />
          </Form.Item>
          <Form.Item
            name="expected_benefit"
            label={t("organizations.capabilityExpectedBenefit")}
            rules={[{ required: true }]}
          >
            <Input.TextArea rows={2} maxLength={4000} />
          </Form.Item>
          <Form.Item
            name="skills"
            label={t("organizations.capabilitySkills")}
            extra={t("organizations.capabilityOnePerLine")}
          >
            <Input.TextArea rows={2} maxLength={4000} />
          </Form.Item>
          <Form.Item
            name="knowledge"
            label={t("organizations.capabilityKnowledge")}
            extra={t("organizations.capabilityOnePerLine")}
          >
            <Input.TextArea rows={2} maxLength={4000} />
          </Form.Item>
          <Form.Item
            name="operating_guidance"
            label={t("organizations.capabilityGuidance")}
            extra={t("organizations.capabilityOnePerLine")}
          >
            <Input.TextArea rows={2} maxLength={4000} />
          </Form.Item>
          <Form.Item
            name="skill_package_ids"
            label={t("organizations.authorizedSkillPackages")}
            extra={t("organizations.capabilityResourceHint")}
          >
            <Select
              mode="multiple"
              options={authorizedWorkerResources.skills.map((resource) => ({
                value: resource.id,
                label: resource.name,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="knowledge_base_ids"
            label={t("organizations.authorizedKnowledgeBases")}
            extra={t("organizations.capabilityResourceHint")}
          >
            <Select
              mode="multiple"
              options={authorizedWorkerResources.knowledge_bases.map(
                (resource) => ({ value: resource.id, label: resource.name }),
              )}
            />
          </Form.Item>
          <Form.Item
            name="risk_notes"
            label={t("organizations.capabilityRiskNotes")}
          >
            <Input.TextArea rows={2} maxLength={4000} />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        open={sceneModalOpen}
        title={t(
          editingScene
            ? "organizations.editScene"
            : "organizations.createScene",
        )}
        onCancel={() => setSceneModalOpen(false)}
        onOk={() => sceneForm.submit()}
        confirmLoading={saving}
        destroyOnClose
      >
        <Form
          form={sceneForm}
          layout="vertical"
          onFinish={(values) => void saveScene(values)}
        >
          <Form.Item
            name="name"
            label={t("organizations.sceneName")}
            rules={[{ required: true }]}
          >
            <Input maxLength={120} />
          </Form.Item>
          {!editingScene && (
            <>
              <Form.Item
                name="position_id"
                label={t("organizations.position")}
                rules={[{ required: true }]}
              >
                <Select
                  options={
                    chart?.positions
                      .filter((position) =>
                        position.assignments.some(
                          (item) => item.subject_type === "agent",
                        ),
                      )
                      .map((position) => ({
                        value: position.position_id,
                        label: position.title,
                      })) ?? []
                  }
                />
              </Form.Item>
              <Form.Item
                name="agent_id"
                label={t("organizations.agent")}
                rules={[{ required: true }]}
              >
                <Select options={assignedAgents} />
              </Form.Item>
            </>
          )}
          <Form.Item
            name="prompt"
            label={t("organizations.scenePrompt")}
            rules={[{ required: true }]}
          >
            <Input.TextArea rows={6} maxLength={20000} />
          </Form.Item>
          <Form.Item
            name="mcp_servers"
            label={t("organizations.mcpServers")}
            extra={t("organizations.mcpServersHint")}
          >
            <Input placeholder="monitoring, asset_inventory" />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        open={Boolean(versionsModalScene)}
        title={t("organizations.versionHistory")}
        onCancel={() => setVersionsModalScene(undefined)}
        footer={null}
        width={760}
        destroyOnClose
      >
        <Typography.Paragraph type="secondary">
          {versionsModalScene?.name} · v{versionsModalScene?.version}
        </Typography.Paragraph>
        <Table
          rowKey="version"
          size="small"
          pagination={{ pageSize: 5 }}
          dataSource={sceneVersions}
          columns={[
            {
              title: t("organizations.sceneVersion"),
              dataIndex: "version",
              render: (version: number) => `v${version}`,
            },
            {
              title: t("organizations.updatedAt"),
              dataIndex: "created_at",
              render: (ts: number) => formatServerDateTime(ts, timezone),
            },
            {
              title: t("organizations.promptSnapshot"),
              dataIndex: "prompt",
              render: (prompt: string) => (
                <Typography.Paragraph ellipsis={{ rows: 3, expandable: true }}>
                  {prompt}
                </Typography.Paragraph>
              ),
            },
            {
              title: t("organizations.mcpServers"),
              dataIndex: "mcp_servers",
              render: (servers: string[]) => servers.join(", ") || "—",
            },
            {
              title: t("organizations.sceneEnabled"),
              dataIndex: "enabled",
              render: (enabled: number | boolean) =>
                t(enabled ? "organizations.enabled" : "organizations.disabled"),
            },
          ]}
        />
      </Modal>
    </div>
  );
}
