import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Drawer,
  Form,
  Input,
  Popconfirm,
  Select,
  Space,
  Steps,
  Table,
  Tag,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";
import {
  agentIdentityApi,
  type SourceIdentityState,
} from "@/api/modules/agentIdentity";
import {
  externalKnowledgeApi,
  type ExternalKnowledgeSource,
} from "@/api/modules/externalKnowledge";
import { saveBlobAsFile } from "@/utils/saveBlobAsFile";
import {
  archiveName,
  canGenerate,
  generateAgentKeyPair,
  isAgentId,
  keyPairArchive,
  randomAgentId,
  type AgentKeyPair,
} from "./agentKeygen";

interface Props {
  source: ExternalKnowledgeSource | null;
  onClose: () => void;
  onSaved: () => void;
}
interface Values {
  agentId: string;
  externalAgentId: string;
  privateKey?: string;
}
export default function SimpleAgentBindingDrawer({
  source,
  onClose,
  onSaved,
}: Props) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const k = "externalKnowledge.simple.";
  const [form] = Form.useForm<Values>();
  const [state, setState] = useState<SourceIdentityState | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [query, setQuery] = useState(""),
    [result, setResult] = useState<string | null>(null);
  const [newId, setNewId] = useState(""),
    [generated, setGenerated] = useState<AgentKeyPair | null>(null);
  const selected = Form.useWatch("agentId", form);
  const file = useRef<HTMLInputElement>(null);
  const generation = useRef(0);
  const load = useCallback(async () => {
    if (!source) return;
    const id = generation.current;
    const next = await agentIdentityApi.state(source.source_id);
    if (id !== generation.current) return;
    setState(next);
  }, [source]);
  useEffect(() => {
    generation.current++;
    setState(null);
    setError("");
    setApiKey("");
    setQuery("");
    setResult(null);
    setGenerated(null);
    setNewId(canGenerate() ? randomAgentId() : "");
    form.resetFields();
    if (source) void load().catch(() => setError(t(k + "loadFailed")));
    return () => {
      generation.current++;
    };
  }, [source, load, form, t]);
  async function act(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : t(k + "failed"));
    } finally {
      setBusy(false);
    }
  }
  async function saveServiceKey() {
    await act(async () => {
      if (!source || !apiKey.trim()) return;
      await externalKnowledgeApi.update(source.source_id, {
        api_key: apiKey.trim(),
      });
      setApiKey("");
      onSaved();
      message.success(t(k + "saved"));
    });
  }
  async function connect() {
    let v: Values;
    try {
      v = await form.validateFields();
    } catch {
      return;
    }
    const pem = v.privateKey ?? "";
    form.setFieldValue("privateKey", "");
    await act(async () => {
      if (!source) return;
      await agentIdentityApi.connect(
        source.source_id,
        v.agentId,
        v.externalAgentId.trim(),
        pem,
      );
      await load();
      onSaved();
      message.success(t(k + "connected"));
    });
  }
  async function generate() {
    await act(async () => {
      const pair = await generateAgentKeyPair(newId);
      await saveBlobAsFile(await keyPairArchive(pair), archiveName(pair));
      setGenerated(pair);
      form.setFieldsValue({
        externalAgentId: pair.agentId,
        privateKey: pair.privatePem,
      });
      setResult(null);
    });
  }
  const active = state?.agents.filter((a) => a.externalAgentId) ?? [];
  const step = active.length ? 2 : 1;
  return (
    <Drawer
      open={!!source}
      title={t(k + "title", { name: source?.display_name })}
      width={680}
      onClose={onClose}
      closable={!busy}
      maskClosable={!busy}
      destroyOnClose
    >
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <Steps
          size="small"
          current={step}
          items={[
            {
              title: t(k + "stepGenerate"),
              description: t(k + "stepGenerateHelp"),
            },
            {
              title: t(k + "stepImport"),
              description: t(k + "stepImportHelp"),
            },
            { title: t(k + "stepBind"), description: t(k + "stepBindHelp") },
          ]}
        />
        <Card size="small" title={t(k + "keygenTitle")}>
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Typography.Text type="secondary">
              {t(k + "keygenHelp")}
            </Typography.Text>
            <Space.Compact style={{ width: "100%" }}>
              <Input
                value={newId}
                disabled={busy}
                style={{ flex: 1 }}
                onChange={(e) => setNewId(e.target.value)}
                placeholder={t(k + "keygenIdPlaceholder")}
              />
              <Button
                disabled={busy || !canGenerate()}
                onClick={() => setNewId(randomAgentId())}
              >
                {t(k + "randomAgentId")}
              </Button>
              <Button
                type="primary"
                loading={busy}
                disabled={!canGenerate() || !isAgentId(newId)}
                onClick={() => void generate()}
              >
                {t(k + "generate")}
              </Button>
            </Space.Compact>
            {!canGenerate() && (
              <Alert type="warning" showIcon message={t(k + "keygenUnsafe")} />
            )}
            {generated && (
              <Space direction="vertical" size={4}>
                <Typography.Text>{t(k + "generated")}</Typography.Text>
                <Typography.Text copyable={{ tooltips: false }}>
                  {generated.kid}
                </Typography.Text>
                <Typography.Text
                  copyable={{ text: generated.publicJwk, tooltips: false }}
                >
                  {t(k + "copyJwk")}
                </Typography.Text>
              </Space>
            )}
          </Space>
        </Card>
        {error && <Alert type="error" showIcon message={error} />}
        {!state?.runtimeReady && (
          <Alert type="warning" showIcon message={t(k + "runtimeMissing")} />
        )}
        <Form layout="vertical" disabled={busy || !state}>
          <Form.Item
            label={t(k + "serviceKey")}
            extra={t(k + "serviceKeyHint")}
          >
            <Input.Password
              autoComplete="new-password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
          </Form.Item>
          <Button
            loading={busy}
            disabled={!state || !apiKey.trim()}
            onClick={() => void saveServiceKey()}
          >
            {t(k + "saveConnection")}
          </Button>
        </Form>
        <Typography.Title level={5}>{t(k + "bindings")}</Typography.Title>
        <Table
          size="small"
          pagination={false}
          rowKey="agentId"
          dataSource={active}
          columns={[
            { title: t(k + "systemAgent"), dataIndex: "name" },
            {
              title: t(k + "agentId"),
              dataIndex: "externalAgentId",
              render: (v: string) => (
                <Typography.Text copyable={{ tooltips: false }}>
                  {v}
                </Typography.Text>
              ),
            },
            {
              title: t(k + "state"),
              render: (_, row) => (
                <Tag color={row.activeKid ? "green" : "default"}>
                  {t(k + (row.activeKid ? "verified" : "pending"))}
                </Tag>
              ),
            },
            {
              title: t(k + "actions"),
              render: (_, row) => (
                <Space>
                  <Button
                    size="small"
                    disabled={busy}
                    onClick={() => {
                      form.setFieldsValue({
                        agentId: row.agentId,
                        externalAgentId: row.externalAgentId,
                        privateKey: "",
                      });
                      setResult(null);
                    }}
                  >
                    {t(k + "select")}
                  </Button>
                  <Popconfirm
                    title={t(k + "unbindHint")}
                    onConfirm={() =>
                      act(async () => {
                        if (!source) return;
                        await agentIdentityApi.disconnect(
                          source.source_id,
                          row.agentId,
                        );
                        await load();
                        onSaved();
                      })
                    }
                  >
                    <Button size="small" danger disabled={busy}>
                      {t(k + "unbind")}
                    </Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
        <Form
          form={form}
          layout="vertical"
          disabled={busy || !state?.runtimeReady}
        >
          <Form.Item
            name="agentId"
            label={t(k + "systemAgent")}
            rules={[{ required: true }]}
          >
            <Select
              showSearch
              optionFilterProp="label"
              options={state?.agents.map((a) => ({
                value: a.agentId,
                label: `${a.name} · ${a.agentId}`,
              }))}
              onChange={(id) => {
                const a = state?.agents.find((a) => a.agentId === id);
                form.setFieldsValue({
                  externalAgentId: a?.externalAgentId ?? "",
                  privateKey: "",
                });
                setResult(null);
              }}
            />
          </Form.Item>
          <Form.Item
            name="externalAgentId"
            label={t(k + "agentId")}
            extra={t(k + "idHint")}
            rules={[
              { required: true },
              {
                pattern:
                  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
                message: t(k + "uuidError"),
              },
            ]}
          >
            <Input disabled={!!active.find((a) => a.agentId === selected)} />
          </Form.Item>
          <Form.Item label={t(k + "privateKey")} extra={t(k + "privateHint")}>
            <input
              ref={file}
              type="file"
              style={{ display: "none" }}
              accept=".pem,.key"
              hidden
              onChange={(e) => {
                const input = e.target,
                  f = input.files?.[0];
                input.value = "";
                if (!f) return;
                if (f.size > 16384) {
                  setError(t(k + "keyTooLarge"));
                  return;
                }
                void f
                  .text()
                  .then((text) => form.setFieldValue("privateKey", text));
              }}
            />
            <Button disabled={busy} onClick={() => file.current?.click()}>
              {t(k + "importPrivate")}
            </Button>
            <Form.Item
              name="privateKey"
              noStyle
              rules={[
                {
                  required: !active.some(
                    (a) => a.agentId === selected && a.activeKid,
                  ),
                  message: t(k + "privateRequired"),
                },
              ]}
            >
              <Input.TextArea
                rows={4}
                autoComplete="off"
                placeholder={t(k + "privatePlaceholder")}
                style={{ marginTop: 8 }}
              />
            </Form.Item>
          </Form.Item>
          <Button
            type="primary"
            loading={busy}
            disabled={!state?.runtimeReady}
            onClick={() => void connect()}
          >
            {t(k + "saveAndTest")}
          </Button>
        </Form>
        <Space.Compact style={{ width: "100%" }}>
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t(k + "query")}
          />
          <Button
            disabled={
              busy ||
              !query.trim() ||
              !active.some((a) => a.agentId === selected && a.activeKid)
            }
            onClick={() =>
              void act(async () => {
                if (!source || !selected) return;
                setResult(null);
                const r = await agentIdentityApi.search(
                  source.source_id,
                  selected,
                  query,
                );
                setResult(
                  r.chunks.map((c) => c.content).join("\n\n") || t(k + "empty"),
                );
              })
            }
          >
            {t(k + "testSearch")}
          </Button>
        </Space.Compact>
        {result !== null && (
          <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>
            {result}
          </Typography.Paragraph>
        )}
      </Space>
    </Drawer>
  );
}
