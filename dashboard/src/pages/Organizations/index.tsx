import { Building2, Pencil, Plus, Trash2, UserRoundPlus, UserRoundX } from "lucide-react";
import {
  Button, Card, Empty, Form, Input, Modal, Popconfirm, Select, Space, Spin, Table, Tabs,
  Tag, Typography, message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  organizationsApi, type OrganizationChart, type OrganizationDepartment,
  type OrganizationPosition, type OrganizationRecord,
} from "../../api/modules/organizations";

type ModalKind = "organization" | "department" | "position" | "member" | "appointment";
type AssignmentKind = "user" | "agent";

export default function OrganizationsPage() {
  const { t } = useTranslation();
  const [form] = Form.useForm();
  const [organizations, setOrganizations] = useState<OrganizationRecord[]>([]);
  const [organizationId, setOrganizationId] = useState<string>();
  const [chart, setChart] = useState<OrganizationChart>();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [modalKind, setModalKind] = useState<ModalKind>();
  const [editingDepartment, setEditingDepartment] = useState<OrganizationDepartment>();
  const [editingPosition, setEditingPosition] = useState<OrganizationPosition>();
  const [assignPositionId, setAssignPositionId] = useState<string>();

  const load = useCallback(async (preferredId?: string) => {
    setLoading(true);
    try {
      const rows = await organizationsApi.list();
      setOrganizations(rows);
      const selected = rows.find((row) => row.organization_id === (preferredId ?? organizationId)) ?? rows[0];
      setOrganizationId(selected?.organization_id);
      setChart(selected ? await organizationsApi.chart(selected.organization_id) : undefined);
    } catch {
      message.error(t("organizations.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [organizationId, t]);

  useEffect(() => { void load(); }, [load]);

  const canManage = chart?.organization.member_role === "owner" || chart?.organization.member_role === "manager";
  const canManageMembers = chart?.organization.member_role === "owner";
  const departmentNames = useMemo(
    () => new Map(chart?.departments.map((row) => [row.department_id, row.name]) ?? []),
    [chart?.departments],
  );
  const positionNames = useMemo(
    () => new Map(chart?.positions.map((row) => [row.position_id, row.title]) ?? []),
    [chart?.positions],
  );

  function openModal(
    kind: ModalKind,
    initial?: Record<string, unknown>,
    department?: OrganizationDepartment,
    position?: OrganizationPosition,
  ) {
    setModalKind(kind);
    setEditingDepartment(department);
    setEditingPosition(position);
    setAssignPositionId(typeof initial?.position_id === "string" ? initial.position_id : undefined);
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
          name: String(values.name ?? ""), code: String(values.code ?? ""),
          description: String(values.description ?? ""),
          parent_department_id: (values.parent_department_id as string | undefined) || null,
        };
        if (editingDepartment) {
          await organizationsApi.updateDepartment(organizationId, editingDepartment.department_id, body);
        } else await organizationsApi.createDepartment(organizationId, body);
        await load(organizationId);
      } else if (modalKind === "position" && organizationId) {
        const body = {
          title: String(values.title ?? ""), code: String(values.code ?? ""),
          department_id: (values.department_id as string | undefined) || null,
          reports_to_position_id: (values.reports_to_position_id as string | undefined) || null,
          duties: String(values.duties ?? ""),
          capabilities: String(values.capabilities ?? "").split(",").map((s) => s.trim()).filter(Boolean),
        };
        if (editingPosition) {
          await organizationsApi.updatePosition(organizationId, editingPosition.position_id, body);
        } else await organizationsApi.createPosition(organizationId, body);
        await load(organizationId);
      } else if (modalKind === "member" && organizationId) {
        await organizationsApi.addMember(
          organizationId, Number(values.user_id), values.role === "manager" ? "manager" : "member",
        );
        await load(organizationId);
      } else if (modalKind === "appointment" && organizationId && assignPositionId) {
        await organizationsApi.assign(
          organizationId, assignPositionId, values.subject_type as AssignmentKind,
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
      title: t("organizations.parentDepartment"), dataIndex: "parent_department_id", key: "parent",
      render: (id: string | null) => id ? departmentNames.get(id) : "—",
    },
    {
      title: t("common.actions"), key: "actions",
      render: (_: unknown, row) => canManage ? <Space>
        <Button aria-label={t("common.edit")} icon={<Pencil size={14} />}
          onClick={() => openModal("department", row as unknown as Record<string, unknown>, row)} />
        <Popconfirm title={t("organizations.deleteDepartmentConfirm")} onConfirm={async () => {
          if (!organizationId) return;
          try { await organizationsApi.deleteDepartment(organizationId, row.department_id); await load(organizationId); }
          catch { message.error(t("organizations.operationFailed")); }
        }}>
          <Button aria-label={t("common.delete")} danger icon={<Trash2 size={14} />} />
        </Popconfirm>
      </Space> : null,
    },
  ];
  const positionColumns: ColumnsType<OrganizationPosition> = [
    { title: t("organizations.position"), dataIndex: "title", key: "title" },
    { title: t("organizations.code"), dataIndex: "code", key: "code" },
    {
      title: t("organizations.department"), dataIndex: "department_id", key: "department",
      render: (id: string | null) => id ? departmentNames.get(id) : "—",
    },
    {
      title: t("organizations.reportsTo"), dataIndex: "reports_to_position_id", key: "reportsTo",
      render: (id: string | null) => id ? positionNames.get(id) : "—",
    },
    {
      title: t("organizations.appointments"), dataIndex: "assignments", key: "assignments",
      render: (items: OrganizationPosition["assignments"]) => items.length
        ? items.map((item) => canManage
          ? <Tag key={item.assignment_id} closable onClose={(event) => {
            event.preventDefault();
            if (!organizationId) return;
            void organizationsApi.unassign(organizationId, item.assignment_id)
              .then(() => load(organizationId))
              .catch(() => message.error(t("organizations.operationFailed")));
          }}>{item.label}</Tag>
          : <Tag key={item.assignment_id}>{item.label}</Tag>)
        : "—",
    },
    {
      title: t("common.actions"), key: "actions",
      render: (_: unknown, row) => canManage ? <Space>
        <Button aria-label={t("common.edit")} icon={<Pencil size={14} />}
          onClick={() => openModal("position", {
            ...row, capabilities: row.capabilities.join(", "),
          } as unknown as Record<string, unknown>, undefined, row)} />
        <Button aria-label={t("organizations.assign")} icon={<UserRoundPlus size={14} />}
          onClick={() => openModal("appointment", { position_id: row.position_id })} />
        <Popconfirm title={t("organizations.deletePositionConfirm")} onConfirm={async () => {
          if (!organizationId) return;
          try { await organizationsApi.deletePosition(organizationId, row.position_id); await load(organizationId); }
          catch { message.error(t("organizations.operationFailed")); }
        }}>
          <Button aria-label={t("common.delete")} danger icon={<Trash2 size={14} />} />
        </Popconfirm>
      </Space> : null,
    },
  ];
  const memberColumns = [
    {
      title: t("organizations.person"), key: "name",
      render: (_: unknown, row: OrganizationChart["members"][number]) => row.display_name || row.username,
    },
    { title: t("organizations.userId"), dataIndex: "user_id", key: "user_id" },
    {
      title: t("organizations.memberRole"), dataIndex: "role", key: "role",
      render: (role: string) => t("organizations.roles." + role),
    },
    {
      title: t("common.actions"), key: "actions",
      render: (_: unknown, row: OrganizationChart["members"][number]) =>
        canManageMembers && row.role !== "owner" ? <Popconfirm
          title={t("organizations.removeMemberConfirm")}
          onConfirm={async () => {
            if (!organizationId) return;
            try { await organizationsApi.removeMember(organizationId, row.user_id); await load(organizationId); }
            catch { message.error(t("organizations.operationFailed")); }
          }}>
          <Button aria-label={t("organizations.removeMember")} danger icon={<UserRoundX size={14} />} />
        </Popconfirm> : null,
    },
  ];

  const modalTitle = modalKind === "organization"
    ? (chart ? t("organizations.editOrganization") : t("organizations.createOrganization"))
    : modalKind === "department"
      ? (editingDepartment ? t("organizations.editDepartment") : t("organizations.addDepartment"))
      : modalKind === "position"
        ? (editingPosition ? t("organizations.editPosition") : t("organizations.addPosition"))
        : modalKind === "member" ? t("organizations.addMember") : t("organizations.assign");

  return <div style={{ padding: 24, maxWidth: 1400, margin: "0 auto" }}>
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div>
        <Typography.Title level={2}>
          <Building2 size={24} style={{ marginRight: 8, verticalAlign: "middle" }} />
          {t("organizations.title")}
        </Typography.Title>
        <Typography.Paragraph type="secondary">{t("organizations.description")}</Typography.Paragraph>
      </div>
      <Card>
        <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
          <Select aria-label={t("organizations.selectOrganization")} style={{ minWidth: 280 }}
            value={organizationId} placeholder={t("organizations.selectOrganization")}
            options={organizations.map((item) => ({ value: item.organization_id, label: item.name }))}
            onChange={(value) => { setOrganizationId(value); void load(value); }} />
          <Space wrap>
            {chart && canManageMembers && <Button icon={<Pencil size={14} />}
              onClick={() => openModal("organization", chart.organization as unknown as Record<string, unknown>)}>
              {t("organizations.editOrganization")}
            </Button>}
            {canManage && <>
              <Button icon={<Plus size={14} />} onClick={() => openModal("department")}>{t("organizations.addDepartment")}</Button>
              <Button type="primary" icon={<Plus size={14} />} onClick={() => openModal("position")}>{t("organizations.addPosition")}</Button>
            </>}
            {canManageMembers && <Button icon={<UserRoundPlus size={14} />} onClick={() => openModal("member")}>{t("organizations.addMember")}</Button>}
            {chart && canManageMembers && <Popconfirm title={t("organizations.deleteOrganizationConfirm")}
              onConfirm={async () => {
                try { await organizationsApi.remove(chart.organization.organization_id); setChart(undefined); setOrganizationId(undefined); await load(); }
                catch { message.error(t("organizations.operationFailed")); }
              }}>
              <Button danger icon={<Trash2 size={14} />}>{t("common.delete")}</Button>
            </Popconfirm>}
            {!chart && <Button type="primary" icon={<Plus size={14} />} onClick={() => openModal("organization")}>{t("organizations.createOrganization")}</Button>}
          </Space>
        </Space>
      </Card>

      {loading ? <Spin /> : !chart
        ? <Card><Empty description={t("organizations.empty")}>
            <Button type="primary" onClick={() => openModal("organization")}>{t("organizations.createOrganization")}</Button>
          </Empty></Card>
        : <Tabs items={[
            { key: "chart", label: t("organizations.organizationChart"), children:
              <Space direction="vertical" size="large" style={{ width: "100%" }}>
                <Card title={t("organizations.departments")}>
                  <Table rowKey="department_id" size="small" pagination={false}
                    dataSource={chart.departments} columns={departmentColumns} />
                </Card>
                <Card title={t("organizations.positions")}>
                  <Table rowKey="position_id" size="small" pagination={false}
                    dataSource={chart.positions} columns={positionColumns} />
                </Card>
              </Space> },
            { key: "members", label: t("organizations.members"), children:
              <Card><Table rowKey="user_id" size="small" pagination={false}
                dataSource={chart.members} columns={memberColumns} /></Card> },
          ]} /> }
    </Space>

    <Modal open={Boolean(modalKind)} title={modalTitle} onCancel={closeModal}
      onOk={() => form.submit()} confirmLoading={saving} destroyOnClose>
      <Form form={form} layout="vertical" onFinish={(values) => void submit(values)}>
        {modalKind === "organization" && <>
          <Form.Item name="name" label={t("organizations.name")} rules={[{ required: true }]}>
            <Input maxLength={120} />
          </Form.Item>
          <Form.Item name="description" label={t("organizations.descriptionField")}>
            <Input.TextArea rows={3} maxLength={2000} />
          </Form.Item>
        </>}
        {modalKind === "department" && <>
          <Form.Item name="name" label={t("organizations.department")} rules={[{ required: true }]}><Input maxLength={120} /></Form.Item>
          <Form.Item name="code" label={t("organizations.code")}><Input maxLength={80} /></Form.Item>
          <Form.Item name="description" label={t("organizations.descriptionField")}><Input.TextArea rows={2} /></Form.Item>
          <Form.Item name="parent_department_id" label={t("organizations.parentDepartment")}>
            <Select allowClear options={chart?.departments
              .filter((item) => item.department_id !== editingDepartment?.department_id)
              .map((item) => ({ value: item.department_id, label: item.name })) ?? []} />
          </Form.Item>
        </>}
        {modalKind === "position" && <>
          <Form.Item name="title" label={t("organizations.position")} rules={[{ required: true }]}><Input maxLength={120} /></Form.Item>
          <Form.Item name="code" label={t("organizations.code")}><Input maxLength={80} /></Form.Item>
          <Form.Item name="department_id" label={t("organizations.department")}>
            <Select allowClear options={chart?.departments.map((item) => ({ value: item.department_id, label: item.name })) ?? []} />
          </Form.Item>
          <Form.Item name="reports_to_position_id" label={t("organizations.reportsTo")}>
            <Select allowClear options={chart?.positions.filter((item) => item.position_id !== editingPosition?.position_id)
              .map((item) => ({ value: item.position_id, label: item.title })) ?? []} />
          </Form.Item>
          <Form.Item name="duties" label={t("organizations.duties")}><Input.TextArea rows={3} maxLength={8000} /></Form.Item>
          <Form.Item name="capabilities" label={t("organizations.capabilities")}><Input placeholder={t("organizations.capabilitiesHint")} /></Form.Item>
        </>}
        {modalKind === "member" && <>
          <Form.Item name="user_id" label={t("organizations.userId")} rules={[{ required: true }]}>
            <Input type="number" min={1} />
          </Form.Item>
          <Form.Item name="role" label={t("organizations.memberRole")} initialValue="member">
            <Select options={[
              { value: "member", label: t("organizations.roles.member") },
              { value: "manager", label: t("organizations.roles.manager") },
            ]} />
          </Form.Item>
        </>}
        {modalKind === "appointment" && <>
          <Typography.Paragraph type="secondary">{positionNames.get(assignPositionId ?? "")}</Typography.Paragraph>
          <Form.Item name="subject_type" label={t("organizations.appointmentType")} initialValue="user">
            <Select options={[
              { value: "user", label: t("organizations.human") },
              { value: "agent", label: t("organizations.agent") },
            ]} />
          </Form.Item>
          <Form.Item name="subject_id" label={t("organizations.assigneeId")} rules={[{ required: true }]}>
            <Input placeholder={t("organizations.assigneeIdHint")} />
          </Form.Item>
        </>}
      </Form>
    </Modal>
  </div>;
}
