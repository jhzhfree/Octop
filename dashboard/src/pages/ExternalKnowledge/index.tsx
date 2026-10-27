import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useTranslation } from "react-i18next";

import {
  externalKnowledgeApi,
  type ExternalKnowledgeChunk,
  type ExternalKnowledgeSource,
  type ExternalSourceCreateBody,
} from "@/api/modules/externalKnowledge";

const TARGET_TYPES = [
  { value: "agent", label: "Agent" },
  { value: "knowledge_base", label: "Knowledge base" },
];

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

export default function ExternalKnowledgePage() {
  const { t } = useTranslation();
  const { message } = App.useApp();

  const [sources, setSources] = useState<ExternalKnowledgeSource[]>([]);
  const [kinds, setKinds] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<ExternalKnowledgeSource | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [form] = Form.useForm<SourceFormValues>();

  const [testing, setTesting] = useState<ExternalKnowledgeSource | null>(null);
  const [testQuery, setTestQuery] = useState("");
  const [testChunks, setTestChunks] = useState<ExternalKnowledgeChunk[]>([]);
  const [testingLoading, setTestingLoading] = useState(false);

  const [bindingSource, setBindingSource] =
    useState<ExternalKnowledgeSource | null>(null);
  const [bindType, setBindType] = useState<string>("agent");
  const [bindTarget, setBindTarget] = useState("");

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

  const columns = useMemo<ColumnsType<ExternalKnowledgeSource>>(
    () => [
      {
        title: t("externalKnowledge.name"),
        dataIndex: "display_name",
        render: (name: string, row) => (
          <Space direction="vertical" size={0}>
            <Typography.Text strong>{name}</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {row.endpoint}
            </Typography.Text>
          </Space>
        ),
      },
      {
        title: t("externalKnowledge.kind"),
        dataIndex: "kind",
        width: 160,
        render: (kind: string) => <Tag>{kind}</Tag>,
      },
      {
        title: t("externalKnowledge.ref"),
        dataIndex: "ref",
        width: 160,
        render: (ref: string) => ref || "-",
      },
      {
        title: t("externalKnowledge.bindings"),
        dataIndex: "bindings",
        width: 220,
        render: (_value: unknown, row) =>
          (row.bindings ?? []).length === 0 ? (
            <Typography.Text type="secondary">-</Typography.Text>
          ) : (
            <Space size={4} wrap>
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
            </Space>
          ),
      },
      {
        title: t("externalKnowledge.enabled"),
        dataIndex: "enabled",
        width: 100,
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
        width: 260,
        render: (_value: unknown, row) => (
          <Space size={4} wrap>
            <Button size="small" onClick={() => probe(row)}>
              {t("externalKnowledge.probe")}
            </Button>
            <Button
              size="small"
              onClick={() => {
                setTesting(row);
                setTestChunks([]);
                setTestQuery("");
              }}
            >
              {t("externalKnowledge.test")}
            </Button>
            <Button size="small" onClick={() => setBindingSource(row)}>
              {t("externalKnowledge.bind")}
            </Button>
            <Button size="small" onClick={() => openEdit(row)}>
              {t("externalKnowledge.edit")}
            </Button>
            <Button size="small" danger onClick={() => void removeSource(row)}>
              {t("externalKnowledge.delete")}
            </Button>
          </Space>
        ),
      },
    ],
    [openEdit, probe, removeSource, t, toggleEnabled, unbind],
  );

  return (
    <div>
      <Space
        style={{
          width: "100%",
          justifyContent: "space-between",
          marginBottom: 16,
        }}
        align="center"
      >
        <Space direction="vertical" size={0}>
          <Typography.Title level={4} style={{ margin: 0 }}>
            {t("externalKnowledge.title")}
          </Typography.Title>
          <Typography.Text type="secondary">
            {t("externalKnowledge.description")}
          </Typography.Text>
        </Space>
        <Button type="primary" onClick={openCreate}>
          {t("externalKnowledge.new")}
        </Button>
      </Space>

      <Table<ExternalKnowledgeSource>
        rowKey="source_id"
        loading={loading}
        columns={columns}
        dataSource={sources}
        pagination={false}
        locale={{
          emptyText: <Empty description={t("externalKnowledge.empty")} />,
        }}
      />

      <Drawer
        open={!!bindingSource}
        width={420}
        title={t("externalKnowledge.bindTitle", {
          name: bindingSource?.display_name ?? "",
        })}
        onClose={() => setBindingSource(null)}
      >
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Select
            style={{ width: "100%" }}
            value={bindType}
            onChange={setBindType}
            options={TARGET_TYPES}
          />
          <Input
            value={bindTarget}
            onChange={(event) => setBindTarget(event.target.value)}
            placeholder={t("externalKnowledge.targetPlaceholder")}
          />
          <Button type="primary" block onClick={() => void submitBinding()}>
            {t("externalKnowledge.bind")}
          </Button>
          {(bindingSource?.bindings ?? []).map((binding) => (
            <Tag
              key={`${binding.target_type}-${binding.target_id}`}
              closable
              onClose={() =>
                bindingSource &&
                void unbind(
                  bindingSource,
                  binding.target_type,
                  binding.target_id,
                )
              }
            >
              {binding.target_type}: {binding.target_id}
            </Tag>
          ))}
        </Space>
      </Drawer>

      <Drawer
        open={!!testing}
        width={520}
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
            onClick={() => void runTest()}
          >
            {t("externalKnowledge.runTest")}
          </Button>
          {testChunks.length === 0 ? (
            <Empty description={t("externalKnowledge.noChunks")} />
          ) : (
            testChunks.map((chunk) => (
              <div key={chunk.id}>
                <Tag color="blue">
                  {chunk.origin} {chunk.score.toFixed(2)}
                </Tag>
                <Typography.Text type="secondary">
                  {chunk.source || chunk.ref}
                </Typography.Text>
                <Typography.Paragraph style={{ marginTop: 4 }}>
                  {chunk.content}
                </Typography.Paragraph>
              </div>
            ))
          )}
        </Space>
      </Drawer>

      <Drawer
        open={formOpen}
        width={480}
        title={
          editing ? t("externalKnowledge.edit") : t("externalKnowledge.new")
        }
        onClose={() => setFormOpen(false)}
        footer={
          <Space style={{ width: "100%", justifyContent: "flex-end" }}>
            <Button onClick={() => setFormOpen(false)}>
              {t("externalKnowledge.cancel")}
            </Button>
            <Button type="primary" onClick={() => void submitForm()}>
              {t("externalKnowledge.save")}
            </Button>
          </Space>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="display_name"
            label={t("externalKnowledge.name")}
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="kind" label={t("externalKnowledge.kind")}>
            <Select
              options={kinds.map((kind) => ({ value: kind, label: kind }))}
            />
          </Form.Item>
          <Form.Item
            name="endpoint"
            label={t("externalKnowledge.endpoint")}
            rules={[{ required: true }]}
            extra={t("externalKnowledge.endpointHint")}
          >
            <Input placeholder="https://akb.example.com" />
          </Form.Item>
          <Form.Item name="api_key" label={t("externalKnowledge.apiKey")}>
            <Input.Password
              placeholder={editing ? t("externalKnowledge.apiKeyHint") : ""}
            />
          </Form.Item>
          <Form.Item name="ref" label={t("externalKnowledge.ref")}>
            <Input placeholder="HR-POLICY" />
          </Form.Item>
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
            name="allow_insecure_http"
            label={t("externalKnowledge.allowInsecure")}
            valuePropName="checked"
            extra={t("externalKnowledge.allowInsecureHint")}
          >
            <Switch size="small" />
          </Form.Item>
          <Form.Item
            name="selector_argument"
            label={t("externalKnowledge.selector")}
          >
            <Input placeholder="knowledgeBaseCode" />
          </Form.Item>
          <Form.Item
            name="shared"
            label={t("externalKnowledge.shared")}
            valuePropName="checked"
          >
            <Switch size="small" />
          </Form.Item>
        </Form>
      </Drawer>
    </div>
  );
}
