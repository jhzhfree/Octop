import {
  useCallback,
  useEffect,
  useState,
  useRef,
  type ChangeEvent,
} from "react";
import {
  Alert,
  App,
  Button,
  Descriptions,
  Collapse,
  Drawer,
  Form,
  Input,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";
import {
  agentIdentityApi,
  type AgentPublicKey,
  type IdentityConfiguration,
  type SourceIdentityState,
  type VerifiedAgentStatus,
} from "@/api/modules/agentIdentity";
import {
  externalKnowledgeApi,
  type ExternalKnowledgeChunk,
  type ExternalKnowledgeSource,
} from "@/api/modules/externalKnowledge";

import { platformSetup, readConnection, SetupFileError } from "./identitySetup";

interface Props {
  source: ExternalKnowledgeSource | null;
  onClose: () => void;
  onSaved: () => void;
}
type FormValues = IdentityConfiguration & { apiKey?: string };

function download(name: string, data: unknown) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export default function AgentIdentityDrawer({
  source,
  onClose,
  onSaved,
}: Props) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<FormValues>();
  const setupFile = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<SourceIdentityState | null>(null);
  const [loading, setLoading] = useState(false),
    [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const [selectedAgent, setSelectedAgent] = useState<string>();
  const [candidate, setCandidate] = useState<string>();
  const [challengeText, setChallengeText] = useState(""),
    [proof, setProof] = useState("");
  const [verification, setVerification] = useState<VerifiedAgentStatus | null>(
    null,
  );
  const [query, setQuery] = useState(""),
    [chunks, setChunks] = useState<ExternalKnowledgeChunk[]>([]);
  const key = "externalKnowledge.identity.";
  const agent = state?.agents.find((row) => row.agentId === selectedAgent);

  const load = useCallback(async () => {
    if (!source) return;
    setLoading(true);
    setFailure("");
    try {
      const next = await agentIdentityApi.state(source.source_id);
      setState(next);
      form.setFieldsValue({
        mode: next.mode,
        policyId: next.policyId,
        applicationPrincipalId: next.applicationPrincipalId,
        issuer: next.issuer,
        audience: next.audience,
      });
    } catch {
      setFailure(t(key + "loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [source, form, t]);

  useEffect(() => {
    setState(null);
    setSelectedAgent(undefined);
    setCandidate(undefined);
    setProof("");
    setChallengeText("");
    setVerification(null);
    setQuery("");
    setChunks([]);
    form.resetFields();
    if (source) void load();
  }, [source, load, form]);

  const act = async (operation: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setFailure("");
    try {
      await operation();
    } catch (error) {
      setFailure(
        error instanceof Error ? error.message : t(key + "operationFailed"),
      );
    } finally {
      setBusy(false);
    }
  };
  const importConnection = (event: ChangeEvent<HTMLInputElement>) => {
    const target = event.target,
      file = target.files?.[0];
    if (!file || !state) return;
    void act(async () => {
      try {
        if (file.size > 16384) throw new SetupFileError("invalid");
        const values = readConnection(await file.text(), state);
        form.setFieldsValue(values);
        message.success(t(key + "setupImported"));
      } catch (error) {
        if (error instanceof SetupFileError)
          throw new Error(
            t(
              key +
                (error.reason === "mismatch"
                  ? "setupMismatch"
                  : "setupInvalid"),
            ),
          );
        throw error;
      } finally {
        target.value = "";
      }
    });
  };
  const save = async () => {
    let values: FormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    await act(async () => {
      if (!source) return;
      if (values.apiKey?.trim())
        await externalKnowledgeApi.update(source.source_id, {
          api_key: values.apiKey.trim(),
        });
      await agentIdentityApi.configure(source.source_id, {
        mode: values.mode,
        policyId: values.policyId.trim(),
        applicationPrincipalId: values.applicationPrincipalId.trim(),
        issuer: values.issuer.trim(),
        audience: values.audience.trim(),
      });
      form.setFieldValue("apiKey", "");
      await load();
      onSaved();
      message.success(t(key + "saved"));
    });
  };
  const prepare = (rotate: boolean) =>
    act(async () => {
      if (!source || !agent) return;
      await agentIdentityApi.prepare(source.source_id, agent.agentId, rotate);
      await load();
      setCandidate(undefined);
      setVerification(null);
      setProof("");
      message.success(t(key + "prepared"));
    });
  const createProof = () =>
    act(async () => {
      if (!source || !agent || !candidate) return;
      setProof("");
      if (challengeText.length > 16384) throw new Error("challenge too large");
      const challenge = JSON.parse(challengeText) as Record<string, unknown>;
      if (
        !challenge ||
        Array.isArray(challenge) ||
        typeof challenge !== "object"
      )
        throw new Error("invalid challenge");
      const result = await agentIdentityApi.prove(
        source.source_id,
        agent.agentId,
        candidate,
        challenge,
      );
      setProof(result.registrationProof);
      await load();
    });
  const activate = (kid: string) =>
    act(async () => {
      if (!source || !agent) return;
      setVerification(null);
      const result = await agentIdentityApi.activate(
        source.source_id,
        agent.agentId,
        kid,
      );
      setVerification(result.status);
      await load();
      message.success(t(key + "verified"));
    });
  const probe = () =>
    act(async () => {
      if (!source || !agent) return;
      setVerification(null);
      const result = await agentIdentityApi.probe(
        source.source_id,
        agent.agentId,
      );
      setVerification(result.status);
      message.success(t(key + "verified"));
    });
  const search = () =>
    act(async () => {
      if (!source || !agent || !query.trim()) return;
      setChunks([]);
      const result = await agentIdentityApi.search(
        source.source_id,
        agent.agentId,
        query.trim(),
      );
      setChunks(result.chunks);
    });
  const publicBundle = (row: AgentPublicKey) => {
    if (!agent) return;
    download(`agent-${agent.agentId}-public.json`, {
      format: "akb-agent-key-registration/v1",
      ...row.owner,
      runtimeAgentKey: agent.agentId,
      publicJwk: row.publicJwk,
    });
  };

  return (
    <Drawer
      open={!!source}
      onClose={() => {
        if (!busy && !loading) onClose();
      }}
      closable={!busy && !loading}
      maskClosable={!busy && !loading}
      keyboard={!busy && !loading}
      width={860}
      title={t(key + "title")}
      destroyOnClose
    >
      <Spin spinning={loading}>
        <Space direction="vertical" size="large" style={{ width: "100%" }}>
          <Alert
            type="info"
            showIcon
            message={t(key + "intro")}
            description={t(key + "flow")}
          />
          {failure && (
            <Alert
              type="error"
              showIcon
              message={failure}
              closable
              onClose={() => setFailure("")}
            />
          )}
          {state && (
            <>
              {!state.runtimeReady && (
                <Alert
                  type="warning"
                  showIcon
                  message={t(key + "runtimeUnavailable")}
                  description={t(key + "runtimeHelp")}
                />
              )}
              <div
                style={{
                  padding: 16,
                  border: "1px solid var(--ant-color-border, #ddd)",
                  borderRadius: 8,
                }}
              >
                <Typography.Title level={5}>
                  {t(key + "guidedTitle")}
                </Typography.Title>
                <Typography.Paragraph>
                  {t(key + "setupStep1")}
                </Typography.Paragraph>
                <Button
                  onClick={() =>
                    download("akb-setup.json", platformSetup(state))
                  }
                >
                  {t(key + "downloadSetup")}
                </Button>
                <Typography.Paragraph style={{ marginTop: 16 }}>
                  {t(key + "setupStep2")}
                </Typography.Paragraph>
                <input
                  hidden
                  ref={setupFile}
                  type="file"
                  accept=".json,application/json"
                  aria-label={t(key + "importConnection")}
                  onChange={importConnection}
                />
                <Button
                  disabled={busy}
                  onClick={() => setupFile.current?.click()}
                >
                  {t(key + "importConnection")}
                </Button>
                <Typography.Paragraph
                  type="secondary"
                  style={{ marginTop: 12, marginBottom: 0 }}
                >
                  {t(key + "setupPrivacy")}
                </Typography.Paragraph>
              </div>
              <Descriptions bordered size="small" column={1}>
                <Descriptions.Item label={t(key + "platform")}>
                  Octop {state.sdkVersion && <Tag>SDK {state.sdkVersion}</Tag>}
                </Descriptions.Item>
                <Descriptions.Item label={t(key + "installation")}>
                  <Typography.Text copyable>
                    {state.installationId}
                  </Typography.Text>
                </Descriptions.Item>
                <Descriptions.Item label={t(key + "workspace")}>
                  <Typography.Text copyable>
                    {state.workspaceId}
                  </Typography.Text>
                </Descriptions.Item>
                <Descriptions.Item label={t(key + "endpoint")}>
                  {source?.endpoint}
                </Descriptions.Item>
              </Descriptions>
              <Form
                form={form}
                layout="vertical"
                initialValues={{
                  mode: "LEGACY",
                  policyId: "",
                  applicationPrincipalId: "",
                  issuer: "",
                  audience: "akb-external-api",
                }}
              >
                <Typography.Title level={5}>
                  {t(key + "policyTitle")}
                </Typography.Title>
                <Collapse
                  style={{ marginBottom: 20 }}
                  items={[
                    {
                      key: "parameters",
                      label: t(key + "advancedParameters"),
                      forceRender: true,
                      children: (
                        <div>
                          {" "}
                          <Form.Item
                            label={t(key + "appId")}
                            name="applicationPrincipalId"
                            rules={[
                              {
                                pattern: /^$|^[1-9][0-9]{0,18}$/,
                                message: t(key + "numericId"),
                              },
                            ]}
                          >
                            <Input autoComplete="off" />
                          </Form.Item>
                          <Form.Item
                            label={t(key + "policyId")}
                            name="policyId"
                            rules={[
                              {
                                pattern: /^$|^[1-9][0-9]{0,18}$/,
                                message: t(key + "numericId"),
                              },
                            ]}
                          >
                            <Input autoComplete="off" />
                          </Form.Item>
                          <Form.Item
                            label={t(key + "issuer")}
                            name="issuer"
                            rules={[{ required: true }, { type: "url" }]}
                          >
                            <Input />
                          </Form.Item>
                          <Form.Item
                            label={t(key + "audience")}
                            name="audience"
                            rules={[{ required: true }]}
                          >
                            <Input />
                          </Form.Item>
                        </div>
                      ),
                    },
                  ]}
                />
                <Form.Item
                  label={t(key + "serviceKey")}
                  name="apiKey"
                  extra={t(key + "serviceKeyHint")}
                >
                  <Input.Password
                    autoComplete="new-password"
                    disabled={!!source?.instance_id}
                  />
                </Form.Item>
                <Form.Item
                  label={t(key + "mode")}
                  name="mode"
                  extra={t(key + "modeHelp")}
                >
                  <Select
                    options={[
                      { value: "LEGACY", label: t(key + "legacy") },
                      { value: "AGENT_KEY_RS256_V2", label: t(key + "v2") },
                    ]}
                  />
                </Form.Item>
                <Button
                  type="primary"
                  loading={busy}
                  onClick={() => void save()}
                >
                  {t(key + "save")}
                </Button>
              </Form>
              <Typography.Title level={5}>
                {t(key + "agentsTitle")}
              </Typography.Title>
              <Select
                disabled={busy}
                aria-label={t(key + "agent")}
                style={{ width: "100%" }}
                placeholder={t(key + "chooseAgent")}
                value={selectedAgent}
                options={state.agents.map((row) => ({
                  value: row.agentId,
                  label: `${row.name} (${row.agentId})`,
                }))}
                onChange={(value) => {
                  setSelectedAgent(value);
                  setCandidate(undefined);
                  setChallengeText("");
                  setProof("");
                  setVerification(null);
                  setChunks([]);
                }}
              />
              {agent && (
                <>
                  <Descriptions bordered size="small" column={1}>
                    <Descriptions.Item label={t(key + "runtimeAgent")}>
                      <Typography.Text copyable>
                        {agent.agentId}
                      </Typography.Text>
                    </Descriptions.Item>
                    <Descriptions.Item label={t(key + "externalAgent")}>
                      <Typography.Text copyable={!!agent.externalAgentId}>
                        {agent.externalAgentId || t(key + "notPrepared")}
                      </Typography.Text>
                    </Descriptions.Item>
                  </Descriptions>
                  {agent.unavailable && (
                    <Alert type="error" message={t(key + "keyUnavailable")} />
                  )}
                  <Space wrap>
                    <Button
                      loading={busy}
                      disabled={!state.runtimeReady}
                      onClick={() => void prepare(false)}
                    >
                      {t(key + "prepare")}
                    </Button>
                    <Button
                      loading={busy}
                      disabled={!state.runtimeReady || !agent.activeKid}
                      onClick={() => void prepare(true)}
                    >
                      {t(key + "rotate")}
                    </Button>
                    <Button
                      loading={busy}
                      disabled={!state.runtimeReady || !agent.activeKid}
                      onClick={() => void probe()}
                    >
                      {t(key + "probe")}
                    </Button>
                  </Space>
                  <Table<AgentPublicKey>
                    size="small"
                    rowKey="kid"
                    dataSource={agent.keys}
                    pagination={false}
                    scroll={{ x: 620 }}
                    columns={[
                      {
                        title: t(key + "kid"),
                        dataIndex: "kid",
                        width: 260,
                        render: (kid: string) => (
                          <Typography.Text
                            copyable
                            ellipsis
                            style={{ maxWidth: 240 }}
                          >
                            {kid}
                          </Typography.Text>
                        ),
                      },
                      {
                        title: t(key + "keyState"),
                        render: (_, row) => (
                          <Tag
                            color={
                              row.kid === agent.activeKid ? "green" : "default"
                            }
                          >
                            {row.kid === agent.activeKid
                              ? t(key + "selected")
                              : t(key + "states." + row.localState)}
                          </Tag>
                        ),
                      },
                      {
                        title: t(key + "actions"),
                        render: (_, row) => (
                          <Space wrap>
                            <Button
                              size="small"
                              onClick={() => publicBundle(row)}
                            >
                              {t(key + "downloadPublic")}
                            </Button>
                            {["PENDING_REGISTRATION", "READY"].includes(
                              row.localState,
                            ) && (
                              <Button
                                size="small"
                                loading={busy}
                                onClick={() => void activate(row.kid)}
                              >
                                {t(key + "activate")}
                              </Button>
                            )}
                          </Space>
                        ),
                      },
                    ]}
                  />
                  {!!agent.keys.length && (
                    <>
                      <Typography.Title level={5}>
                        {t(key + "registrationTitle")}
                      </Typography.Title>
                      <Alert
                        type="info"
                        message={t(key + "registrationHint")}
                      />
                      <Select
                        style={{ width: "100%" }}
                        value={candidate}
                        aria-label={t(key + "chooseKey")}
                        disabled={busy}
                        placeholder={t(key + "chooseKey")}
                        onChange={(value) => {
                          setCandidate(value);
                          setProof("");
                        }}
                        options={agent.keys
                          .filter((row) =>
                            ["PENDING_REGISTRATION", "READY"].includes(
                              row.localState,
                            ),
                          )
                          .map((row) => ({ value: row.kid, label: row.kid }))}
                      />
                      <Input.TextArea
                        disabled={busy}
                        aria-label={t(key + "challenge")}
                        rows={5}
                        value={challengeText}
                        onChange={(event) => {
                          setChallengeText(event.target.value);
                          setProof("");
                        }}
                        placeholder={t(key + "challenge")}
                        maxLength={16384}
                      />
                      <Button
                        loading={busy}
                        disabled={
                          !candidate || !challengeText.trim() || !state.policyId
                        }
                        onClick={() => void createProof()}
                      >
                        {t(key + "prove")}
                      </Button>
                      {proof && (
                        <>
                          <Input.TextArea
                            aria-label={t(key + "proof")}
                            readOnly
                            rows={4}
                            value={proof}
                          />
                          <Space>
                            <Typography.Text copyable={{ text: proof }}>
                              {t(key + "copyProof")}
                            </Typography.Text>
                            <Button
                              onClick={() =>
                                download("registration-proof.json", {
                                  registrationProof: proof,
                                })
                              }
                            >
                              {t(key + "downloadProof")}
                            </Button>
                          </Space>
                        </>
                      )}
                    </>
                  )}
                  {verification && (
                    <Alert
                      type="success"
                      showIcon
                      message={t(key + "verified")}
                      description={
                        <Space direction="vertical">
                          <span>
                            {t(key + "remoteAgent")}:{" "}
                            {verification.agentPrincipalId}
                          </span>
                          <span>
                            {t(key + "kid")}: {verification.kid}
                          </span>
                        </Space>
                      }
                    />
                  )}
                  <Typography.Title level={5}>
                    {t(key + "searchTitle")}
                  </Typography.Title>
                  <Input.TextArea
                    rows={2}
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder={t(key + "query")}
                    maxLength={4000}
                  />
                  <Button
                    disabled={!agent.activeKid || !query.trim()}
                    loading={busy}
                    onClick={() => void search()}
                  >
                    {t(key + "search")}
                  </Button>
                  {chunks.map((chunk) => (
                    <div
                      key={chunk.id}
                      style={{
                        padding: 12,
                        border: "1px solid var(--ant-color-border, #ddd)",
                        borderRadius: 8,
                      }}
                    >
                      <Typography.Text type="secondary">
                        {chunk.source} · {chunk.score.toFixed(2)}
                      </Typography.Text>
                      <Typography.Paragraph
                        style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}
                      >
                        {chunk.content}
                      </Typography.Paragraph>
                    </div>
                  ))}
                </>
              )}
              <Alert type="warning" message={t(key + "boundary")} />
            </>
          )}
        </Space>
      </Spin>
    </Drawer>
  );
}
