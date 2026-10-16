import {
  Building2,
  Check,
  Pencil,
  Play,
  Plus,
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
          const [nextChart, nextScenes, nextRuns] = await Promise.all([
            organizationsApi.chart(selected.organization_id),
            organizationsApi.scenes(selected.organization_id),
            organizationsApi.sceneRuns(selected.organization_id),
          ]);
          setChart(nextChart);
          setScenes(nextScenes);
          setSceneRuns(nextRuns);
        } else {
          setChart(undefined);
          setScenes([]);
          setSceneRuns([]);
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
