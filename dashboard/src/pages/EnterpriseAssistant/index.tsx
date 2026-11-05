import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Drawer,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Steps,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import PageShell from "../../layouts/PageShell";
import { useAgent } from "../../context/AgentContext";
import { authApi } from "../../api/modules/auth";
import {
  enterpriseAssistantApi as api,
  type Features,
  type ConnectionConfig,
  type ConnectionKind,
  type JobConfig,
  type Resource,
  type WorkKind,
  type WorkRun,
} from "../../api/modules/enterpriseAssistant";
import styles from "./index.module.less";

const moduleFor: Record<string, keyof Features> = {
  inspection: "inspection",
  jira: "work_items",
  oa: "work_items",
  report: "reports",
  wecom: "reports",
  vmware: "vmware",
};
const kinds: ConnectionKind[] = ["inspection", "jira", "oa", "wecom", "vmware"];
const jobKinds: WorkKind[] = ["inspection", "jira", "oa", "report", "vmware"];
const activeStates = new Set(["queued", "running", "approved", "executing"]);

export default function EnterpriseAssistant() {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const { activeAgentId } = useAgent();
  const [flags, setFlags] = useState<Features>();
  const [admin, setAdmin] = useState(false);
  const [connections, setConnections] = useState<Resource<ConnectionConfig>[]>(
    [],
  );
  const [jobs, setJobs] = useState<Resource<JobConfig>[]>([]);
  const [runs, setRuns] = useState<WorkRun[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [connectionModal, setConnectionModal] = useState(false);
  const [jobModal, setJobModal] = useState(false);
  const [editingConnection, setEditingConnection] =
    useState<Resource<ConnectionConfig>>();
  const [editingJob, setEditingJob] = useState<Resource<JobConfig>>();
  const [selected, setSelected] = useState<string>();
  const [draft, setDraft] = useState("");
  const [dirty, setDirty] = useState(false);
  const [connectionForm] = Form.useForm<
    ConnectionConfig & { secret: string }
  >();
  const [jobForm] = Form.useForm<JobConfig>();
  const [featureForm] = Form.useForm<Features & { origins: string }>();
  const connectionKind = Form.useWatch("kind", connectionForm) as
    | ConnectionKind
    | undefined;
  const inspectionTransport = Form.useWatch(
    "inspection_transport",
    connectionForm,
  );
  const isSsh =
    connectionKind === "inspection" && inspectionTransport === "ssh";
  const jobKind = Form.useWatch("kind", jobForm) as WorkKind | undefined;
  const run = runs.find((r) => r.id === selected);
  const label = (key: string) => t(`enterpriseAssistant.${key}`);

  const load = useCallback(async () => {
    try {
      const current = await api.features();
      setFlags(current);
      if (current.enabled) {
        const [c, j, r] = await Promise.all([
          api.connections(),
          api.jobs(),
          api.runs(),
        ]);
        setConnections(c);
        setJobs(j);
        setRuns(r);
      } else {
        setConnections([]);
        setJobs([]);
        setRuns([]);
      }
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);
  useEffect(() => {
    void authApi.me().then((u) => setAdmin(u.role === "admin"));
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  const perform = async (
    action: () => Promise<unknown>,
    close?: () => void,
  ) => {
    setBusy(true);
    try {
      await action();
      close?.();
      await load();
      message.success(label("saved"));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const openConnection = (old?: Resource<ConnectionConfig>) => {
    setEditingConnection(old);
    connectionForm.resetFields();
    connectionForm.setFieldsValue(
      old
        ? { ...old.config, secret: "" }
        : {
            name: "",
            kind: "inspection",
            url: "",
            auth: "none",
            jira_api: "2",
            username_selector: "input[name=username]",
            password_selector: "input[type=password]",
            login_selector: "button[type=submit]",
          },
    );
    setConnectionModal(true);
  };
  const openJob = (old?: Resource<JobConfig>) => {
    setEditingJob(old);
    jobForm.resetFields();
    jobForm.setFieldsValue(
      old?.config ?? {
        kind: "inspection",
        agent_id: activeAgentId ?? "",
        enabled: false,
        cron: "",
        timezone: "Asia/Shanghai",
        vm_name: "",
        source_job_ids: [],
      },
    );
    setJobModal(true);
  };
  const confirm = (value: WorkRun, approve: boolean) => {
    modal.confirm({
      title: label(approve ? "confirmTitle" : "rejectTitle"),
      content: label(
        value.status === "unknown" ? "reconcileHint" : "confirmHint",
      ),
      onOk: () => perform(() => api.decide(value, approve)),
    });
  };
  const field = (
    name: keyof ConnectionConfig,
    required = false,
    placeholder?: string,
  ) => (
    <Form.Item
      key={name}
      name={name}
      label={label(name)}
      rules={required ? [{ required: true }] : []}
    >
      <Input placeholder={placeholder} autoComplete="off" />
    </Form.Item>
  );
  const enabledKind = (kind: string) =>
    flags?.enabled && Boolean(flags[moduleFor[kind]]);

  return (
    <PageShell
      title={label("title")}
      subtitle={label("subtitle")}
      agentScoped
      actions={<Link to="/tasks">{label("back")}</Link>}
    >
      <div className={styles.page}>
        {error && (
          <Alert
            type="error"
            showIcon
            message={label("error")}
            description={error}
            closable
            onClose={() => setError("")}
          />
        )}
        {!flags ? (
          <Spin />
        ) : (
          <>
            <Steps
              size="small"
              items={[
                {
                  title: label("stepEnable"),
                  status: flags.enabled ? "finish" : "process",
                },
                {
                  title: label("stepConnection"),
                  status: connections.length
                    ? "finish"
                    : flags.enabled
                    ? "process"
                    : "wait",
                },
                {
                  title: label("stepJob"),
                  status: jobs.length ? "finish" : "wait",
                },
                {
                  title: label("stepReview"),
                  status: runs.length ? "process" : "wait",
                },
              ]}
            />
            {admin && (
              <Card title={label("modules")}>
                <Form
                  form={featureForm}
                  layout="vertical"
                  key={JSON.stringify(flags)}
                  initialValues={{
                    ...flags,
                    origins: flags.allowed_origins.join("\n"),
                  }}
                  onFinish={(v) =>
                    perform(() =>
                      api.setFeatures({
                        enabled: v.enabled,
                        inspection: v.inspection,
                        work_items: v.work_items,
                        reports: v.reports,
                        vmware: v.vmware,
                        allow_http: v.allow_http,
                        allowed_origins: v.origins.split(/\s+/).filter(Boolean),
                      }),
                    )
                  }
                >
                  <div className={styles.switches}>
                    {(
                      [
                        "enabled",
                        "inspection",
                        "work_items",
                        "reports",
                        "vmware",
                        "allow_http",
                      ] as const
                    ).map((key) => (
                      <Form.Item
                        key={key}
                        name={key}
                        valuePropName="checked"
                        label={label(key)}
                      >
                        <Switch />
                      </Form.Item>
                    ))}
                  </div>
                  <Form.Item
                    name="origins"
                    label={label("origins")}
                    extra={label("originsHint")}
                  >
                    <Input.TextArea
                      rows={2}
                      placeholder="https://jira.company.example"
                    />
                  </Form.Item>
                  <Button htmlType="submit" type="primary" loading={busy}>
                    {label("saveModules")}
                  </Button>
                </Form>
              </Card>
            )}
            {!flags.enabled ? (
              <Alert
                showIcon
                type="info"
                message={label("disabled")}
                description={label("disabledHint")}
              />
            ) : (
              <>
                <Alert
                  showIcon
                  type="info"
                  message={label("safety")}
                  description={
                    <>
                      <span>{label("safetyHint")}</span>{" "}
                      <Link to="/knowledge-bases">{label("knowledge")}</Link>
                    </>
                  }
                />
                <Tabs
                  items={[
                    {
                      key: "connections",
                      label: label("connections"),
                      children: (
                        <Card
                          extra={
                            <Button
                              type="primary"
                              onClick={() => openConnection()}
                            >
                              {label("addConnection")}
                            </Button>
                          }
                        >
                          <Table
                            rowKey="id"
                            dataSource={connections}
                            scroll={{ x: 600 }}
                            columns={[
                              {
                                title: label("name"),
                                dataIndex: ["config", "name"],
                              },
                              {
                                title: label("kind"),
                                render: (_, c) => (
                                  <Tag>{label(c.config.kind)}</Tag>
                                ),
                              },
                              {
                                title: label("url"),
                                dataIndex: ["config", "url"],
                                ellipsis: true,
                              },
                              {
                                title: label("secretState"),
                                render: (_, c) =>
                                  label(
                                    c.has_secret ? "secretStored" : "noSecret",
                                  ),
                              },
                              {
                                title: label("actions"),
                                render: (_, c) => (
                                  <Button
                                    disabled={!enabledKind(c.config.kind)}
                                    onClick={() => openConnection(c)}
                                  >
                                    {label("edit")}
                                  </Button>
                                ),
                              },
                            ]}
                          />
                        </Card>
                      ),
                    },
                    {
                      key: "jobs",
                      label: label("jobs"),
                      children: (
                        <Card
                          extra={
                            <Button
                              type="primary"
                              disabled={!activeAgentId || !connections.length}
                              onClick={() => openJob()}
                            >
                              {label("addJob")}
                            </Button>
                          }
                        >
                          {!activeAgentId && (
                            <Alert
                              message={label("selectAgent")}
                              type="warning"
                            />
                          )}
                          <Table
                            rowKey="id"
                            dataSource={jobs.filter(
                              (j) =>
                                !activeAgentId ||
                                j.config.agent_id === activeAgentId,
                            )}
                            scroll={{ x: 700 }}
                            columns={[
                              {
                                title: label("name"),
                                dataIndex: ["config", "name"],
                              },
                              {
                                title: label("kind"),
                                render: (_, j) => label(j.config.kind),
                              },
                              {
                                title: label("schedule"),
                                render: (_, j) =>
                                  j.config.enabled && j.config.cron
                                    ? `${j.config.cron} · ${j.config.timezone}`
                                    : label("manual"),
                              },
                              {
                                title: label("nextDue"),
                                render: (_, j) =>
                                  enabledKind(j.config.kind) && j.next_due
                                    ? new Date(
                                        j.next_due * 1000,
                                      ).toLocaleString()
                                    : "—",
                              },
                              {
                                title: label("actions"),
                                render: (_, j) => (
                                  <Space>
                                    <Button
                                      disabled={!enabledKind(j.config.kind)}
                                      onClick={() => openJob(j)}
                                    >
                                      {label("edit")}
                                    </Button>
                                    <Button
                                      disabled={!enabledKind(j.config.kind)}
                                      loading={busy}
                                      onClick={() =>
                                        perform(() => api.run(j.id))
                                      }
                                    >
                                      {label("run")}
                                    </Button>
                                  </Space>
                                ),
                              },
                            ]}
                          />
                        </Card>
                      ),
                    },
                    {
                      key: "runs",
                      label: label("runs"),
                      children: (
                        <Card>
                          <Table
                            rowKey="id"
                            dataSource={runs.filter(
                              (r) =>
                                !activeAgentId ||
                                r.snapshot.job.agent_id === activeAgentId,
                            )}
                            scroll={{ x: 650 }}
                            columns={[
                              {
                                title: label("name"),
                                render: (_, r) => r.snapshot.job.name,
                              },
                              {
                                title: label("status"),
                                render: (_, r) => (
                                  <Tag
                                    color={
                                      r.status === "awaiting_approval"
                                        ? "orange"
                                        : ["sent", "succeeded"].includes(
                                            r.status,
                                          )
                                        ? "green"
                                        : r.status === "unknown" ||
                                          r.status === "failed"
                                        ? "red"
                                        : "blue"
                                    }
                                  >
                                    {label(`states.${r.status}`)}
                                  </Tag>
                                ),
                              },
                              {
                                title: label("time"),
                                render: (_, r) =>
                                  new Date(
                                    r.created_at * 1000,
                                  ).toLocaleString(),
                              },
                              {
                                title: label("actions"),
                                render: (_, r) => (
                                  <Button
                                    onClick={() => {
                                      setSelected(r.id);
                                      setDraft(r.content);
                                      setDirty(false);
                                    }}
                                  >
                                    {label("detail")}
                                  </Button>
                                ),
                              },
                            ]}
                          />
                        </Card>
                      ),
                    },
                  ]}
                />
              </>
            )}
          </>
        )}
      </div>
      <Modal
        title={label(editingConnection ? "editConnection" : "addConnection")}
        open={connectionModal}
        onCancel={() => setConnectionModal(false)}
        footer={null}
        destroyOnHidden
        width={720}
      >
        <Form
          form={connectionForm}
          layout="vertical"
          onFinish={({ secret, ...config }) =>
            perform(
              () => api.saveConnection(config, secret || "", editingConnection),
              () => setConnectionModal(false),
            )
          }
        >
          <Form.Item
            name="kind"
            label={label("kind")}
            rules={[{ required: true }]}
          >
            <Select
              disabled={!!editingConnection}
              onChange={() =>
                connectionForm.setFieldsValue({
                  secret: "",
                  url: "",
                  username: "",
                  auth: "none",
                  inspection_transport: "http",
                  ssh_host_key: "",
                })
              }
              options={kinds.map((k) => ({
                value: k,
                label: label(k),
                disabled: !enabledKind(k),
              }))}
            />
          </Form.Item>
          {field("name", true)}
          {connectionKind === "inspection" && (
            <>
              <Form.Item
                name="inspection_transport"
                label={label("inspection_transport")}
                initialValue="http"
              >
                <Select
                  onChange={() =>
                    connectionForm.setFieldsValue({
                      secret: "",
                      url: "",
                      auth: "none",
                    })
                  }
                  options={[
                    { value: "http", label: "HTTP JSON" },
                    { value: "ssh", label: "SSH (Linux)" },
                  ]}
                />
              </Form.Item>
              {isSsh && field("ssh_host_key", true, "ssh-ed25519 AAAA...")}
            </>
          )}
          {field(
            "url",
            true,
            connectionKind === "wecom"
              ? "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"
              : isSsh
              ? "ssh://server.company.example:22"
              : "https://service.company.example",
          )}
          <Alert
            message={label(
              `help.${isSsh ? "ssh" : connectionKind ?? "inspection"}`,
            )}
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
          />
          {connectionKind !== "wecom" && (
            <>
              {field("username")}
              {connectionKind !== "oa" &&
                connectionKind !== "vmware" &&
                !isSsh && (
                  <Form.Item name="auth" label={label("auth")}>
                    <Select
                      options={[
                        { value: "none", label: label("authNone") },
                        { value: "bearer", label: "Bearer / PAT" },
                        { value: "basic", label: "Basic" },
                      ]}
                    />
                  </Form.Item>
                )}
            </>
          )}
          <Form.Item
            name="secret"
            label={label(
              isSsh
                ? "sshPrivateKey"
                : connectionKind === "wecom"
                ? "webhookKey"
                : "secret",
            )}
            extra={label(editingConnection ? "secretKeep" : "secretHint")}
          >
            {isSsh ? (
              <Input.TextArea
                rows={4}
                autoComplete="off"
                placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
              />
            ) : (
              <Input.Password autoComplete="new-password" />
            )}
          </Form.Item>
          {connectionKind === "jira" && (
            <>
              {field(
                "jql",
                false,
                "assignee = currentUser() AND resolution = Unresolved",
              )}
              <Form.Item name="jira_api" label={label("jira_api")}>
                <Select
                  options={[
                    { value: "2", label: "Jira Server / Data Center (v2)" },
                    { value: "3", label: "Jira REST v3" },
                  ]}
                />
              </Form.Item>
            </>
          )}
          {connectionKind === "oa" && (
            <div className={styles.fields}>
              {(
                [
                  "username_selector",
                  "password_selector",
                  "login_selector",
                  "ready_selector",
                  "items_selector",
                ] as const
              ).map((n) => field(n, true))}
            </div>
          )}
          {connectionKind === "vmware" && (
            <div className={styles.fields}>
              {(
                [
                  "template_id",
                  "os_label",
                  "folder",
                  "resource_pool",
                  "datastore",
                  "customization_spec",
                ] as const
              ).map((n) => field(n, n !== "customization_spec"))}
            </div>
          )}
          <Button htmlType="submit" type="primary" loading={busy}>
            {label("save")}
          </Button>
        </Form>
      </Modal>
      <Modal
        title={label(editingJob ? "editJob" : "addJob")}
        open={jobModal}
        onCancel={() => setJobModal(false)}
        footer={null}
        destroyOnHidden
        width={720}
      >
        <Form
          form={jobForm}
          layout="vertical"
          onFinish={(config) =>
            perform(
              () =>
                api.saveJob(
                  {
                    ...config,
                    cron: config.cron || "",
                    source_job_ids: config.source_job_ids || [],
                    vm_name: config.vm_name || "",
                  },
                  editingJob,
                ),
              () => setJobModal(false),
            )
          }
        >
          <Form.Item
            name="name"
            label={label("name")}
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="agent_id" label="Agent" rules={[{ required: true }]}>
            <Input readOnly />
          </Form.Item>
          <Form.Item
            name="kind"
            label={label("kind")}
            rules={[{ required: true }]}
          >
            <Select
              onChange={() =>
                jobForm.setFieldsValue({
                  connection_id: undefined,
                  cron: "",
                  source_job_ids: [],
                  vm_name: "",
                })
              }
              options={jobKinds.map((k) => ({
                value: k,
                label: label(k),
                disabled: !enabledKind(k),
              }))}
            />
          </Form.Item>
          <Form.Item
            name="connection_id"
            label={label("connection")}
            rules={[{ required: true }]}
          >
            <Select
              options={connections
                .filter(
                  (c) =>
                    c.config.kind ===
                    (jobKind === "report" ? "wecom" : jobKind),
                )
                .map((c) => ({ value: c.id, label: c.config.name }))}
            />
          </Form.Item>
          {jobKind === "report" && (
            <Form.Item
              name="source_job_ids"
              label={label("sources")}
              rules={[{ required: true }]}
            >
              <Select
                mode="multiple"
                options={jobs
                  .filter(
                    (j) =>
                      ["inspection", "jira", "oa"].includes(j.config.kind) &&
                      j.config.agent_id === jobForm.getFieldValue("agent_id"),
                  )
                  .map((j) => ({ value: j.id, label: j.config.name }))}
              />
            </Form.Item>
          )}
          {jobKind === "vmware" ? (
            <Form.Item
              name="vm_name"
              label={label("vm_name")}
              rules={[{ required: true }, { pattern: /^[a-zA-Z0-9_.-]+$/ }]}
            >
              <Input />
            </Form.Item>
          ) : (
            <>
              <Form.Item
                name="enabled"
                label={label("scheduleEnabled")}
                valuePropName="checked"
              >
                <Switch />
              </Form.Item>
              <Form.Item
                name="cron"
                label={label("cron")}
                extra={label("cronHint")}
              >
                <Input
                  placeholder={
                    jobKind === "report" ? "0 17 * * fri" : "0 9 * * *"
                  }
                />
              </Form.Item>
            </>
          )}
          <Form.Item
            name="timezone"
            label={label("timezone")}
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
          <Button htmlType="submit" type="primary" loading={busy}>
            {label("save")}
          </Button>
        </Form>
      </Modal>
      <Drawer
        title={label("detail")}
        open={!!selected}
        onClose={() => setSelected(undefined)}
        width="min(900px, 100vw)"
      >
        {!run ? (
          <Empty />
        ) : (
          <Space direction="vertical" size="large" style={{ width: "100%" }}>
            <Steps
              direction="vertical"
              size="small"
              current={
                activeStates.has(run.status)
                  ? run.status === "queued"
                    ? 0
                    : run.status === "running"
                    ? 1
                    : 3
                  : run.status === "awaiting_approval"
                  ? 2
                  : 4
              }
              status={
                run.status === "unknown" || run.status === "failed"
                  ? "error"
                  : "process"
              }
              items={[
                { title: label("queuedStep") },
                { title: label("collectStep") },
                { title: label("reviewStep") },
                { title: label("executeStep") },
                { title: label(`states.${run.status}`) },
              ]}
            />
            {run.error_code && (
              <Alert
                type="error"
                message={label("error")}
                description={run.error_code}
              />
            )}
            <Card size="small" title={label("destination")}>
              <Typography.Text>
                {run.snapshot.connection.name} · {run.snapshot.connection.url}
              </Typography.Text>
              <br />
              <Typography.Text type="secondary">{run.id}</Typography.Text>
            </Card>
            {(run.content || run.status === "awaiting_approval") && (
              <>
                <Input.TextArea
                  aria-label={label("draft")}
                  rows={12}
                  value={dirty ? draft : run.content}
                  readOnly={
                    run.status !== "awaiting_approval" ||
                    run.snapshot.job.kind !== "report"
                  }
                  onChange={(e) => {
                    setDraft(e.target.value);
                    setDirty(true);
                  }}
                />
                {dirty && (
                  <Button
                    loading={busy}
                    onClick={() =>
                      perform(
                        () => api.editDraft(run, draft),
                        () => setDirty(false),
                      )
                    }
                  >
                    {label("saveDraft")}
                  </Button>
                )}
              </>
            )}
            {run.status === "awaiting_approval" && (
              <Space>
                <Button
                  type="primary"
                  disabled={dirty || !enabledKind(run.snapshot.job.kind)}
                  loading={busy}
                  onClick={() => confirm(run, true)}
                >
                  {label("approve")}
                </Button>
                <Button
                  disabled={!enabledKind(run.snapshot.job.kind)}
                  onClick={() => confirm(run, false)}
                >
                  {label("reject")}
                </Button>
              </Space>
            )}
            {run.status === "unknown" && (
              <Alert
                type="warning"
                showIcon
                message={label("unknownHint")}
                action={
                  <Button onClick={() => confirm(run, false)}>
                    {label("closeReconciled")}
                  </Button>
                }
              />
            )}
            <Card size="small" title={label("evidence")}>
              <pre className={styles.evidence}>
                {JSON.stringify(run.evidence, null, 2)}
              </pre>
            </Card>
          </Space>
        )}
      </Drawer>
    </PageShell>
  );
}
