import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Button,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Segmented,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  Activity,
  Link2,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  externalKnowledgeApi,
  type ExternalKnowledgeChunk,
  type ExternalKnowledgeSource,
  type ExternalSourceCreateBody,
} from "@/api/modules/externalKnowledge";
import { useIsMobile } from "../../hooks/useIsMobile";
import PageShell from "../../layouts/PageShell";
import { useAgent, type OctopAgent } from "../../context/AgentContext";
import styles from "./index.module.less";
import AgentIdentityDrawer from "./AgentIdentityDrawer";

const TARGET_TYPES = [
  { value: "agent", label: "Agent" },
  { value: "knowledge_base", label: "Knowledge base" },
];

const KIND_COLORS: Record<string, string> = {
  "akb-mcp": "geekblue",
  "akb-rest": "blue",
  ragflow: "purple",
  weknora: "cyan",
  "standard-retrieval": "green",
};

interface SourceFormValues {
  display_name: string;
  kind: string;
  endpoint: string;
  api_key?: string;
  ref?: string;
  top_k: number;
  min_score: number;
  timeout_ms: number;
  allow_insecure_http: boolean;
  selector_argument?: string;
  shared: boolean;
}

/** Bindings store raw ids; resolve an agent id back to a readable label. */
function targetLabel(
  binding: { target_type: string; target_id: string },
  agents: OctopAgent[],
): string {
  if (binding.target_type !== "agent") return binding.target_id;
  const match = agents.find((agent) => agent.agent_id === binding.target_id);
  return match ? `${match.name} · ${binding.target_id}` : binding.target_id;
}

function scoreColor(score: number): string {
  if (score >= 0.7) return "green";
  if (score >= 0.4) return "gold";
  return "default";
}

export default function ExternalKnowledgePage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const isMobile = useIsMobile();

  const [sources, setSources] = useState<ExternalKnowledgeSource[]>([]);
  const [kinds, setKinds] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [identitySource, setIdentitySource] =
    useState<ExternalKnowledgeSource | null>(null);
  const [editing, setEditing] = useState<ExternalKnowledgeSource | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [form] = Form.useForm<SourceFormValues>();

  const [refs, setRefs] = useState<{ ref: string; name: string }[]>([]);
  const [refsLoading, setRefsLoading] = useState(false);
  const insecureHttp = Form.useWatch("allow_insecure_http", form);
  const formKind = Form.useWatch("kind", form);

  const [testing, setTesting] = useState<ExternalKnowledgeSource | null>(null);
  const [testQuery, setTestQuery] = useState("");
  const [testChunks, setTestChunks] = useState<ExternalKnowledgeChunk[]>([]);
  const [testingLoading, setTestingLoading] = useState(false);

  const [bindingSource, setBindingSource] =
    useState<ExternalKnowledgeSource | null>(null);
  const [bindType, setBindType] = useState<string>("agent");
  const [bindTarget, setBindTarget] = useState("");
  // Agents the current user can see; the binding target must be one of them.
  const { agents } = useAgent();

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [rows, kindResult] = await Promise.all([
        externalKnowledgeApi.list(),
        externalKnowledgeApi.kinds(),
      ]);
      setSources(rows);
      setKinds(kindResult.kinds);
    } catch {
      message.error(t("externalKnowledge.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [message, t]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const openCreate = useCallback(() => {
    setEditing(null);
    setRefs([]);
    form.resetFields();
    form.setFieldsValue({
      kind: kinds[0] ?? "akb-mcp",
      top_k: 8,
      min_score: 0,
      timeout_ms: 10000,
      allow_insecure_http: false,
      shared: false,
    });
    setFormOpen(true);
  }, [form, kinds]);

  const openEdit = useCallback(
    (row: ExternalKnowledgeSource) => {
      setEditing(row);
      setRefs([]);
      form.resetFields();
      form.setFieldsValue({
        display_name: row.display_name,
        kind: row.kind,
        endpoint: row.endpoint,
        ref: row.ref,
        top_k: row.top_k,
        min_score: row.min_score,
        timeout_ms: row.timeout_ms,
        allow_insecure_http: row.allow_insecure_http,
        selector_argument: row.selector_argument,
        shared: row.shared,
      });
      setFormOpen(true);
    },
    [form],
  );

  const submitForm = useCallback(async () => {
    const values = await form.validateFields();
    const body: ExternalSourceCreateBody = {
      display_name: values.display_name,
      kind: values.kind,
      endpoint: values.endpoint,
      ref: values.ref ?? "",
      top_k: values.top_k,
      min_score: values.min_score,
      timeout_ms: values.timeout_ms,
      allow_insecure_http: values.allow_insecure_http,
      selector_argument: values.selector_argument ?? "",
      shared: values.shared,
    };
    if (values.api_key) body.api_key = values.api_key;
    try {
      if (editing) {
        await externalKnowledgeApi.update(editing.source_id, body);
        message.success(t("externalKnowledge.updated"));
      } else {
        await externalKnowledgeApi.create(body);
        message.success(t("externalKnowledge.created"));
      }
      setFormOpen(false);
      await refresh();
    } catch {
      message.error(t("externalKnowledge.saveFailed"));
    }
  }, [editing, form, message, refresh, t]);

  const fetchRefs = useCallback(async () => {
    if (!editing) {
      message.info(t("externalKnowledge.fetchRefsAfterSave"));
      return;
    }
    setRefsLoading(true);
    try {
      const result = await externalKnowledgeApi.refs(editing.source_id);
      setRefs(result.refs ?? []);
      if ((result.refs ?? []).length === 0) {
        message.info(t("externalKnowledge.noRefs"));
      }
    } catch {
      message.error(t("externalKnowledge.fetchRefsFailed"));
    } finally {
      setRefsLoading(false);
    }
  }, [editing, message, t]);

  const toggleEnabled = useCallback(
    async (row: ExternalKnowledgeSource, enabled: boolean) => {
      try {
        await externalKnowledgeApi.update(row.source_id, { enabled });
        await refresh();
      } catch {
        message.error(t("externalKnowledge.saveFailed"));
      }
    },
    [message, refresh, t],
  );

  const removeSource = useCallback(
    async (row: ExternalKnowledgeSource) => {
      try {
        await externalKnowledgeApi.remove(row.source_id);
        message.success(t("externalKnowledge.deleted"));
        await refresh();
      } catch {
        message.error(t("externalKnowledge.deleteFailed"));
      }
    },
    [message, refresh, t],
  );

  const probe = useCallback(
    async (row: ExternalKnowledgeSource) => {
      if (row.identity_mode === "AGENT_KEY_RS256_V2") {
        setIdentitySource(row);
        return;
      }
      try {
        const result = await externalKnowledgeApi.probe(row.source_id);
        if (result.ok) {
          message.success(result.message || t("externalKnowledge.probeOk"));
        } else {
          message.error(result.message || result.code);
        }
      } catch {
        message.error(t("externalKnowledge.probeFailed"));
      }
    },
    [message, t],
  );

  const runTest = useCallback(async () => {
    if (!testing || !testQuery.trim()) return;
    setTestingLoading(true);
    try {
      const result = await externalKnowledgeApi.search(
        testing.source_id,
        testQuery.trim(),
      );
      setTestChunks(result.chunks);
      if (result.warnings.length > 0) {
        message.warning(result.warnings.map((item) => item.code).join(", "));
      }
    } catch {
      message.error(t("externalKnowledge.searchFailed"));
    } finally {
      setTestingLoading(false);
    }
  }, [message, t, testQuery, testing]);

  const submitBinding = useCallback(async () => {
    if (!bindingSource || !bindTarget.trim()) return;
    try {
      await externalKnowledgeApi.bind(
        bindingSource.source_id,
        bindType,
        bindTarget.trim(),
      );
      message.success(t("externalKnowledge.bound"));
      setBindTarget("");
      await refresh();
    } catch {
      message.error(t("externalKnowledge.bindFailed"));
    }
  }, [bindTarget, bindType, bindingSource, message, refresh, t]);

  const unbind = useCallback(
    async (
      row: ExternalKnowledgeSource,
      targetType: string,
      targetId: string,
    ) => {
      try {
        await externalKnowledgeApi.unbind(row.source_id, targetType, targetId);
        await refresh();
      } catch {
        message.error(t("externalKnowledge.bindFailed"));
      }
    },
    [message, refresh, t],
  );

  const summary = useMemo(
    () => ({
      total: sources.length,
      enabled: sources.filter((item) => item.enabled).length,
      shared: sources.filter((item) => item.shared).length,
    }),
    [sources],
  );

  const columns = useMemo<ColumnsType<ExternalKnowledgeSource>>(
    () => [
      {
        title: t("externalKnowledge.name"),
        dataIndex: "display_name",
        render: (name: string, row) => (
          <div className={styles.nameCell}>
            <div className={styles.nameRow}>
              <span className={styles.nameText}>{name}</span>
              <Tag color={KIND_COLORS[row.kind] ?? "default"} bordered={false}>
                {row.kind}
              </Tag>
              {row.identity_mode === "AGENT_KEY_RS256_V2" && (
                <Tag color="green">
                  {t("externalKnowledge.identity.v2Badge")}
                </Tag>
              )}
              {!row.has_credentials && (
                <Tooltip title={t("externalKnowledge.noCredentialsHint")}>
                  <Tag color="orange" bordered={false}>
                    {t("externalKnowledge.noCredentials")}
                  </Tag>
                </Tooltip>
              )}
            </div>
            <span className={styles.endpointText}>{row.endpoint}</span>
          </div>
        ),
      },
      {
        title: t("externalKnowledge.ref"),
        dataIndex: "ref",
        width: 180,
        render: (ref: string) =>
          ref ? (
            <Tag className={styles.refTag} bordered={false}>
              {ref}
            </Tag>
          ) : (
            <Typography.Text type="secondary">-</Typography.Text>
          ),
      },
      {
        title: t("externalKnowledge.bindings"),
        dataIndex: "bindings",
        width: 240,
        render: (_value: unknown, row) =>
          (row.bindings ?? []).length === 0 ? (
            <Typography.Text type="secondary">-</Typography.Text>
          ) : (
            <div className={styles.bindingTags}>
              {(row.bindings ?? []).map((binding) => (
                <Tag
                  key={`${binding.target_type}-${binding.target_id}`}
                  closable
                  onClose={() =>
                    void unbind(row, binding.target_type, binding.target_id)
                  }
                >
                  {binding.target_type}: {binding.target_id}
                </Tag>
              ))}
            </div>
          ),
      },
      {
        title: t("externalKnowledge.enabled"),
        dataIndex: "enabled",
        width: 90,
        render: (enabled: boolean, row) => (
          <Switch
            size="small"
            checked={enabled}
            disabled={!row.can_manage}
            onChange={(checked) => void toggleEnabled(row, checked)}
          />
        ),
      },
      {
        title: t("externalKnowledge.actions"),
        key: "actions",
        width: 340,
        render: (_value: unknown, row) => (
          <div className={styles.rowActions}>
            {row.can_manage && ["akb-mcp", "akb-rest"].includes(row.kind) && (
              <Tooltip title={t("externalKnowledge.identity.manage")}>
                <Button
                  size="small"
                  aria-label={t("externalKnowledge.identity.manage")}
                  icon={<ShieldCheck size={15} />}
                  onClick={() => setIdentitySource(row)}
                >
                  {t("externalKnowledge.identity.manage")}
                </Button>
              </Tooltip>
            )}
            <Tooltip title={t("externalKnowledge.probe")}>
              <Button
                type="text"
                className={styles.iconBtn}
                aria-label={t("externalKnowledge.probe")}
                icon={<Activity size={15} />}
                onClick={() => probe(row)}
              />
            </Tooltip>
            <Tooltip title={t("externalKnowledge.test")}>
              <Button
                type="text"
                className={styles.iconBtn}
                aria-label={t("externalKnowledge.test")}
                icon={<Search size={15} />}
                onClick={() => {
                  if (row.identity_mode === "AGENT_KEY_RS256_V2") {
                    setIdentitySource(row);
                    return;
                  }
                  setTesting(row);
                  setTestChunks([]);
                  setTestQuery("");
                }}
              />
            </Tooltip>
            <Tooltip title={t("externalKnowledge.bind")}>
              <Button
                size="small"
                aria-label={t("externalKnowledge.bind")}
                icon={<Link2 size={15} />}
                onClick={() => setBindingSource(row)}
              >
                {t("externalKnowledge.bind")}
              </Button>
            </Tooltip>
            <Tooltip title={t("externalKnowledge.edit")}>
              <Button
                type="text"
                className={styles.iconBtn}
                aria-label={t("externalKnowledge.edit")}
                icon={<Pencil size={15} />}
                onClick={() => openEdit(row)}
              />
            </Tooltip>
            <Popconfirm
              title={t("externalKnowledge.delete")}
              description={t("externalKnowledge.deleteConfirm")}
              okButtonProps={{ danger: true }}
              onConfirm={() => void removeSource(row)}
            >
              <Tooltip title={t("externalKnowledge.delete")}>
                <Button
                  type="text"
                  className={`${styles.iconBtn} ${styles.iconBtnDanger}`}
                  aria-label={t("externalKnowledge.delete")}
                  icon={<Trash2 size={15} />}
                />
              </Tooltip>
            </Popconfirm>
          </div>
        ),
      },
    ],
    [openEdit, probe, removeSource, t, toggleEnabled, unbind],
  );

  return (
    <PageShell
      title={t("externalKnowledge.title")}
      subtitle={t("externalKnowledge.description")}
      fill={!isMobile}
      actions={
        <Button type="primary" icon={<Plus size={15} />} onClick={openCreate}>
          {t("externalKnowledge.new")}
        </Button>
      }
    >
      <div className={styles.layout}>
        <Alert
          showIcon
          type="info"
          message={t("externalKnowledge.identity.platformScopeTitle")}
          description={t("externalKnowledge.identity.platformScopeHelp")}
        />
        <div className={styles.panelHeader}>
          <div className={styles.panelStats}>
            <Tag className={styles.statTag} bordered={false}>
              {t("externalKnowledge.total")}: {summary.total}
            </Tag>
            <Tag className={styles.statTag} color="green" bordered={false}>
              {t("externalKnowledge.enabledCount")}: {summary.enabled}
            </Tag>
            <Tag className={styles.statTag} color="blue" bordered={false}>
              {t("externalKnowledge.sharedCount")}: {summary.shared}
            </Tag>
          </div>
          <div className={styles.panelActions}>
            <Tooltip title={t("externalKnowledge.refresh")}>
              <Button
                className={styles.iconBtn}
                aria-label={t("externalKnowledge.refresh")}
                icon={<RefreshCw size={15} />}
                loading={loading}
                onClick={() => void refresh()}
              />
            </Tooltip>
          </div>
        </div>

        <div className={styles.tableWrap}>
          <Table<ExternalKnowledgeSource>
            rowKey="source_id"
            loading={loading}
            columns={columns}
            dataSource={sources}
            pagination={false}
            scroll={{ x: 960 }}
            locale={{
              emptyText: (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={t("externalKnowledge.empty")}
                >
                  <Button
                    type="primary"
                    icon={<Plus size={15} />}
                    onClick={openCreate}
                  >
                    {t("externalKnowledge.new")}
                  </Button>
                </Empty>
              ),
            }}
          />
        </div>
      </div>

      <AgentIdentityDrawer
        source={identitySource}
        onClose={() => setIdentitySource(null)}
        onSaved={() => void refresh()}
      />

      <Drawer
        className={styles.drawer}
        open={!!bindingSource}
        width={440}
        title={t("externalKnowledge.bindTitle", {
          name: bindingSource?.display_name ?? "",
        })}
        onClose={() => setBindingSource(null)}
      >
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Alert
            type="info"
            showIcon
            message={t("externalKnowledge.identity.bindingHelp")}
          />
          <Segmented
            block
            value={bindType}
            onChange={(value) => {
              // The two target types hold different id shapes, so a value
              // picked for one cannot carry over to the other.
              setBindType(String(value));
              setBindTarget("");
            }}
            options={TARGET_TYPES}
          />
          {bindType === "agent" ? (
            <Select
              style={{ width: "100%" }}
              showSearch
              allowClear
              value={bindTarget || undefined}
              onChange={(value) => setBindTarget(value ?? "")}
              optionFilterProp="label"
              options={agents.map((agent) => ({
                value: agent.agent_id,
                label: `${agent.name} · ${agent.agent_id}`,
              }))}
              placeholder={t("externalKnowledge.selectAgent")}
              notFoundContent={t("externalKnowledge.noAgents")}
            />
          ) : (
            <Input
              value={bindTarget}
              onChange={(event) => setBindTarget(event.target.value)}
              placeholder={t("externalKnowledge.targetPlaceholder")}
            />
          )}
          <Button type="primary" block onClick={() => void submitBinding()}>
            {t("externalKnowledge.bind")}
          </Button>
          <div className={styles.sectionTitle}>
            {t("externalKnowledge.currentBindings")}
          </div>
          {(bindingSource?.bindings ?? []).length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={t("externalKnowledge.noBindings")}
            />
          ) : (
            <div className={styles.bindingList}>
              {(bindingSource?.bindings ?? []).map((binding) => (
                <div
                  key={`${binding.target_type}-${binding.target_id}`}
                  className={styles.bindingRow}
                >
                  <span className={styles.bindingText}>
                    {binding.target_type}: {targetLabel(binding, agents)}
                  </span>
                  <Button
                    size="small"
                    type="link"
                    danger
                    onClick={() =>
                      bindingSource &&
                      void unbind(
                        bindingSource,
                        binding.target_type,
                        binding.target_id,
                      )
                    }
                  >
                    {t("externalKnowledge.unbind")}
                  </Button>
                </div>
              ))}
            </div>
          )}
        </Space>
      </Drawer>

      <Drawer
        className={styles.drawer}
        open={!!testing}
        width={560}
        title={t("externalKnowledge.testTitle", {
          name: testing?.display_name ?? "",
        })}
        onClose={() => setTesting(null)}
      >
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Input.TextArea
            rows={3}
            value={testQuery}
            onChange={(event) => setTestQuery(event.target.value)}
            placeholder={t("externalKnowledge.testPlaceholder")}
          />
          <Button
            type="primary"
            block
            loading={testingLoading}
            disabled={!testQuery.trim()}
            onClick={() => void runTest()}
          >
            {t("externalKnowledge.runTest")}
          </Button>
          {testChunks.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={t("externalKnowledge.noChunks")}
            />
          ) : (
            <Typography.Text type="secondary">
              {t("externalKnowledge.passageCount", {
                count: testChunks.length,
              })}
            </Typography.Text>
          )}
          {testChunks.length > 0 && (
            <div className={styles.chunkList}>
              {testChunks.map((chunk) => (
                <div key={chunk.id} className={styles.chunkCard}>
                  <div className={styles.chunkHeader}>
                    <Tag color={scoreColor(chunk.score)} bordered={false}>
                      {t("externalKnowledge.score")} {chunk.score.toFixed(2)}
                    </Tag>
                    <span className={styles.chunkMeta}>
                      {chunk.source || chunk.ref || chunk.origin}
                    </span>
                  </div>
                  <Typography.Paragraph
                    className={styles.chunkText}
                    ellipsis={{
                      rows: 4,
                      expandable: true,
                      symbol: t("externalKnowledge.expand"),
                    }}
                  >
                    {chunk.content}
                  </Typography.Paragraph>
                </div>
              ))}
            </div>
          )}
        </Space>
      </Drawer>

      <Drawer
        className={styles.drawer}
        open={formOpen}
        width={520}
        title={
          editing ? t("externalKnowledge.edit") : t("externalKnowledge.new")
        }
        onClose={() => setFormOpen(false)}
        footer={
          <div className={styles.drawerFooter}>
            <Button onClick={() => setFormOpen(false)}>
              {t("externalKnowledge.cancel")}
            </Button>
            <Button type="primary" onClick={() => void submitForm()}>
              {t("externalKnowledge.save")}
            </Button>
          </div>
        }
      >
        <Form form={form} layout="vertical" className={styles.form}>
          <div className={styles.sectionTitle}>
            {t("externalKnowledge.basicSection")}
          </div>
          <Form.Item
            name="display_name"
            label={t("externalKnowledge.name")}
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="kind" label={t("externalKnowledge.kind")}>
            <Select
              disabled={editing?.identity_mode === "AGENT_KEY_RS256_V2"}
              options={kinds.map((kind) => ({ value: kind, label: kind }))}
            />
          </Form.Item>
          <Form.Item
            name="endpoint"
            label={t("externalKnowledge.endpoint")}
            rules={[{ required: true }]}
            extra={
              formKind === "standard-retrieval"
                ? t("externalKnowledge.endpointHintFull")
                : t("externalKnowledge.endpointHintRoot")
            }
          >
            <Input
              disabled={editing?.identity_mode === "AGENT_KEY_RS256_V2"}
              placeholder={
                formKind === "standard-retrieval"
                  ? "https://akb.example.com/api/v1/external/kb/retrievals"
                  : "https://akb.example.com"
              }
            />
          </Form.Item>
          <Form.Item name="api_key" label={t("externalKnowledge.apiKey")}>
            <Input.Password
              autoComplete="new-password"
              placeholder={editing ? t("externalKnowledge.apiKeyHint") : ""}
            />
          </Form.Item>
          <Form.Item
            label={t("externalKnowledge.ref")}
            extra={t(
              editing?.identity_mode === "AGENT_KEY_RS256_V2"
                ? "externalKnowledge.identity.v2RefsHint"
                : "externalKnowledge.refHint",
            )}
          >
            <Space.Compact style={{ width: "100%" }}>
              {refs.length > 0 ? (
                <Form.Item name="ref" noStyle>
                  <Select
                    allowClear
                    showSearch
                    optionFilterProp="label"
                    placeholder={t("externalKnowledge.refPickerPlaceholder")}
                    options={refs.map((item) => ({
                      value: item.ref,
                      label: item.name
                        ? `${item.name}（${item.ref}）`
                        : item.ref,
                    }))}
                  />
                </Form.Item>
              ) : (
                <Form.Item name="ref" noStyle>
                  <Input placeholder="HR-POLICY" />
                </Form.Item>
              )}
              <Button
                loading={refsLoading}
                disabled={
                  !editing || editing.identity_mode === "AGENT_KEY_RS256_V2"
                }
                onClick={() => void fetchRefs()}
                title={editing ? "" : t("externalKnowledge.fetchRefsAfterSave")}
              >
                {t("externalKnowledge.fetchRefs")}
              </Button>
            </Space.Compact>
          </Form.Item>

          <div className={styles.sectionTitle}>
            {t("externalKnowledge.retrievalSection")}
          </div>
          <Form.Item name="top_k" label={t("externalKnowledge.topK")}>
            <InputNumber min={1} max={50} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="min_score" label={t("externalKnowledge.minScore")}>
            <InputNumber
              min={0}
              max={1}
              step={0.05}
              style={{ width: "100%" }}
            />
          </Form.Item>
          <Form.Item name="timeout_ms" label={t("externalKnowledge.timeout")}>
            <InputNumber
              min={1000}
              max={60000}
              step={1000}
              style={{ width: "100%" }}
            />
          </Form.Item>
          <Form.Item
            name="selector_argument"
            label={t("externalKnowledge.selector")}
          >
            <Input placeholder="knowledgeBaseCode" />
          </Form.Item>

          <div className={styles.sectionTitle}>
            {t("externalKnowledge.securitySection")}
          </div>
          <div className={styles.formOptions}>
            <div className={styles.formOptionRow}>
              <div className={styles.formOptionCopy}>
                <span className={styles.formOptionLabel}>
                  {t("externalKnowledge.allowInsecure")}
                </span>
                <span className={styles.formOptionHint}>
                  {t("externalKnowledge.allowInsecureHint")}
                </span>
              </div>
              <Form.Item
                name="allow_insecure_http"
                valuePropName="checked"
                noStyle
              >
                <Switch size="small" />
              </Form.Item>
            </div>
            <div className={styles.formOptionRow}>
              <div className={styles.formOptionCopy}>
                <span className={styles.formOptionLabel}>
                  {t("externalKnowledge.shared")}
                </span>
                <span className={styles.formOptionHint}>
                  {t("externalKnowledge.sharedHint")}
                </span>
              </div>
              <Form.Item name="shared" valuePropName="checked" noStyle>
                <Switch size="small" />
              </Form.Item>
            </div>
          </div>
          {insecureHttp && (
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 12 }}
              message={t("externalKnowledge.insecureWarning")}
            />
          )}
        </Form>
      </Drawer>
    </PageShell>
  );
}
