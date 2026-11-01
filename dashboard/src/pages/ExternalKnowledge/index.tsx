import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  AutoComplete,
  Button,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Segmented,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  Activity,
  Info,
  Link2,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { agentIdentityApi } from "@/api/modules/agentIdentity";
import {
  externalKnowledgeApi,
  type ExternalKnowledgeChunk,
  type ExternalKnowledgeConnection,
  type ExternalKnowledgeProbe,
  type ExternalKnowledgeSource,
  type ExternalKnowledgeToolSchema,
  type ExternalSourceCreateBody,
} from "@/api/modules/externalKnowledge";
import { checkHint } from "./diagnostics";
import { apiErrorMessage, parseApiError } from "@/utils/apiError";
import { useServerTimezone } from "@/hooks/useServerTimezone";
import { formatServerIsoDateTime } from "@/utils/formatMessageTime";
import { useIsMobile } from "../../hooks/useIsMobile";
import PageShell from "../../layouts/PageShell";
import { useAgent, type OctopAgent } from "../../context/AgentContext";
import styles from "./index.module.less";
import SimpleAgentBindingDrawer from "./SimpleAgentBindingDrawer";

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
  connection_id?: string;
  display_name: string;
  kind: string;
  endpoint: string;
  api_key?: string;
  auth_type?: "service" | "agent";
  api_key_header?: string;
  ref?: string;
  top_k: number;
  min_score: number;
  timeout_ms: number;
  allow_insecure_http: boolean;
  selector_argument?: string;
  tool_name?: string;
  tool_arguments?: Record<string, string | number | boolean>;
  shared: boolean;
}

/** Arguments are edited as rows of text, typed back only when saved. */
interface ToolArgumentRow {
  key: string;
  value: string;
  required: boolean;
}

// Supplied by the caller on every retrieval: repeating them here could only
// disagree with what the actual query asks for.
const _CONTROLLED_ARGUMENTS = ["query", "question", "topK", "top_k"];

// Which knowledge base the call targets. An MCP tool takes it as an ordinary
// argument, so it is edited together with the other tool arguments and lifted
// into the source's own ref on save — the stored call still carries exactly one
// base selector, and it stays the one the source declares.
const _BASE_ARGUMENTS = ["knowledgeBaseId", "knowledgeBaseCode"];

/** Show the stored base as the argument an MCP tool would take, so it can be
 *  changed where the rest of the tool arguments live. */
function baseArgumentRow(
  ref: string | undefined,
  selector: string | undefined,
): ToolArgumentRow | null {
  const value = (ref || "").trim();
  if (!value) return null;
  const declared = (selector || "").trim();
  return {
    key:
      declared ||
      (/^\d+$/.test(value) ? "knowledgeBaseId" : "knowledgeBaseCode"),
    value,
    required: false,
  };
}

/** The base leads the argument list and never appears twice. */
function withBaseArgument(
  rows: ToolArgumentRow[],
  ref: string | undefined,
  selector: string | undefined,
): ToolArgumentRow[] {
  const base = baseArgumentRow(ref, selector);
  const rest = rows.filter((row) => !_BASE_ARGUMENTS.includes(row.key.trim()));
  return base ? [base, ...rest] : rest;
}

function toArgumentRows(saved: Record<string, string | number | boolean> = {}) {
  return Object.entries(saved).map(([key, value]) => ({
    key,
    value: String(value),
    required: false,
  }));
}

function toArgumentRecord(rows: ToolArgumentRow[]) {
  const record: Record<string, string | number | boolean> = {};
  for (const row of rows) {
    const name = row.key.trim();
    if (!name || _CONTROLLED_ARGUMENTS.includes(name)) continue;
    const raw = row.value.trim();
    if (!raw) continue;
    // Numbers and flags reach the server in their own type; anything else is
    // text, which is what most tool arguments declare anyway.
    if (raw === "true" || raw === "false") record[name] = raw === "true";
    else if (/^-?\d+$/.test(raw)) record[name] = Number(raw);
    else if (/^-?\d+\.\d+$/.test(raw)) record[name] = Number(raw);
    else record[name] = raw;
  }
  return record;
}

/** Build the editable rows a tool asks for, keeping anything already filled in. */
function rowsForTool(
  tool: ExternalKnowledgeToolSchema | undefined,
  kept: ToolArgumentRow[],
): ToolArgumentRow[] {
  const properties = tool?.input_schema?.properties;
  if (!properties || typeof properties !== "object") {
    // No schema to go on: whatever was saved is all we can offer.
    return kept.filter(
      (row) =>
        row.key &&
        row.value !== undefined &&
        !_CONTROLLED_ARGUMENTS.includes(row.key),
    );
  }
  const required = new Set(
    Array.isArray(tool.input_schema.required)
      ? tool.input_schema.required.map((name) => String(name))
      : [],
  );
  return Object.entries(properties as Record<string, Record<string, unknown>>)
    .filter(([name]) => !_CONTROLLED_ARGUMENTS.includes(name))
    .map(([name, spec]) => {
      const existing = kept.find((row) => row.key === name);
      if (existing) return existing;
      const fallback = spec?.default;
      const scalar =
        typeof fallback === "string" ||
        typeof fallback === "number" ||
        typeof fallback === "boolean";
      return {
        key: name,
        value: scalar ? String(fallback) : "",
        required: required.has(name),
      };
    });
}

function toolLabel(tool: ExternalKnowledgeToolSchema) {
  return tool.description ? `${tool.name} — ${tool.description}` : tool.name;
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
  const { message, modal } = App.useApp();
  const timezone = useServerTimezone();
  const isMobile = useIsMobile();

  const [sources, setSources] = useState<ExternalKnowledgeSource[]>([]);
  const [connections, setConnections] = useState<ExternalKnowledgeConnection[]>(
    [],
  );
  const [connectionEditor, setConnectionEditor] = useState<
    ExternalKnowledgeConnection | "new" | null
  >(null);
  const [section, setSection] = useState("connections");
  const [checkingConnection, setCheckingConnection] = useState<string>();
  const openNewConnection = () => {
    connectionForm.resetFields();
    connectionForm.setFieldsValue({
      kind: "akb-mcp",
      auth_type: "service",
      api_key_header: "x-api-key",
    });
    setConnectionEditor("new");
  };
  const checkConnection = async (row: ExternalKnowledgeConnection) => {
    setCheckingConnection(row.connection_id);
    try {
      await externalKnowledgeApi.probeConnection(row.connection_id);
      await refresh();
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("externalKnowledge.probeFailed"), t),
      );
    } finally {
      setCheckingConnection(undefined);
    }
  };
  const [detailConnectionId, setDetailConnectionId] = useState<string>();
  const detailConnection = connections.find(
    (row) => row.connection_id === detailConnectionId,
  );
  const detailResources = sources.filter(
    (row) => row.connection_id === detailConnectionId,
  );
  const confirmImpact = async (row: ExternalKnowledgeConnection) =>
    (row.resource_count === 0 && row.agent_count === 0) ||
    (await new Promise<boolean>((resolve) => {
      modal.confirm({
        title: t("externalKnowledge.impactTitle"),
        content: t("externalKnowledge.impactHelp", {
          resources: row.resource_count,
          agents: row.agent_count,
        }),
        onOk: () => resolve(true),
        onCancel: () => resolve(false),
      });
    }));
  const [conflictTarget, setConflictTarget] = useState<
    "resource" | "connection" | null
  >(null);
  const [connectionForm] = Form.useForm();
  const connectionKind = Form.useWatch("kind", connectionForm) || "akb-mcp";
  const connectionAuth = Form.useWatch("auth_type", connectionForm);
  const [kinds, setKinds] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [identitySource, setIdentitySource] = useState<Pick<
    ExternalKnowledgeSource,
    "source_id" | "connection_id" | "display_name"
  > | null>(null);
  const [editing, setEditing] = useState<ExternalKnowledgeSource | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [form] = Form.useForm<SourceFormValues>();

  const [refs, setRefs] = useState<{ ref: string; name: string }[]>([]);
  const [refsLoading, setRefsLoading] = useState(false);
  const [probing, setProbing] = useState(false);
  const [probeInfoOpen, setProbeInfoOpen] = useState(false);
  const [probeResult, setProbeResult] = useState<ExternalKnowledgeProbe | null>(
    null,
  );
  const insecureHttp = Form.useWatch("allow_insecure_http", form);
  const formKind = Form.useWatch("kind", form) || "akb-mcp";
  const formAuthType = Form.useWatch("auth_type", form);
  const reusedConnection = Form.useWatch("connection_id", form);
  const [catalogAgent, setCatalogAgent] = useState<string>();
  const [catalogAgents, setCatalogAgents] = useState<
    { value: string; label: string }[]
  >([]);
  useEffect(() => {
    setCatalogAgent(undefined);
    setCatalogAgents([]);
    const sourceId =
      editing?.source_id ||
      (reusedConnection ? `connection:${reusedConnection}` : "");
    const mode =
      editing?.auth_type ||
      connections.find((row) => row.connection_id === reusedConnection)
        ?.auth_type;
    if (!formOpen || !sourceId || mode !== "agent") return;
    let cancelled = false;
    void agentIdentityApi
      .state(sourceId)
      .then((state) => {
        if (!cancelled)
          setCatalogAgents(
            state.agents
              .filter((a) => a.externalAgentId && a.activeKid)
              .map((a) => ({ value: a.agentId, label: a.name })),
          );
      })
      .catch((error: unknown) => {
        if (!cancelled)
          message.error(
            apiErrorMessage(error, t("externalKnowledge.loadFailed"), t),
          );
      });
    return () => {
      cancelled = true;
    };
  }, [editing, formOpen, message, t, reusedConnection, connections]);
  const selectedTool = Form.useWatch("tool_name", form);
  // The advertised tools and their argument schemas: what the operator picks
  // from, dropped as soon as the drawer closes because it is upstream state.
  const [toolSchemas, setToolSchemas] = useState<ExternalKnowledgeToolSchema[]>(
    [],
  );
  const [toolArgs, setToolArgs] = useState<ToolArgumentRow[]>([]);
  // A source saved against a tool stays selectable even before the list is
  // fetched again, otherwise re-saving silently clears the operator's choice.
  const toolOptions = useMemo(() => {
    const options = [...toolSchemas];
    if (selectedTool && !options.some((item) => item.name === selectedTool)) {
      options.unshift({
        name: selectedTool,
        description: "",
        input_schema: {},
      });
    }
    return options;
  }, [selectedTool, toolSchemas]);

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
      const [rows, kindResult, connectionRows] = await Promise.all([
        externalKnowledgeApi.list(),
        externalKnowledgeApi.kinds(),
        externalKnowledgeApi.connections(),
      ]);
      setSources(rows);
      setConnections(connectionRows);
      setKinds(kindResult.kinds);
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("externalKnowledge.loadFailed"), t),
      );
    } finally {
      setLoading(false);
    }
  }, [message, t]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const openCreate = useCallback(() => {
    setConflictTarget(null);
    setEditing(null);
    setRefs([]);
    setProbeResult(null);
    setToolSchemas([]);
    setToolArgs([]);
    form.resetFields();
    form.setFieldsValue({
      connection_id:
        connections.find((row) => row.enabled && !row.needs_attention)
          ?.connection_id || "",
      kind:
        connections.find((row) => row.enabled && !row.needs_attention)?.kind ||
        kinds[0] ||
        "akb-mcp",
      auth_type:
        connections.find((row) => row.enabled && !row.needs_attention)
          ?.auth_type === "agent"
          ? "agent"
          : "service",
      api_key_header: "x-api-key",
      endpoint:
        connections.find((row) => row.enabled && !row.needs_attention)
          ?.endpoint || "",
      top_k: 8,
      min_score: 0,
      timeout_ms: 10000,
      allow_insecure_http:
        connections.find((row) => row.enabled && !row.needs_attention)
          ?.allow_insecure_http || false,
      shared: false,
    });
    setFormOpen(true);
  }, [form, kinds, connections]);

  const openEdit = useCallback(
    (row: ExternalKnowledgeSource) => {
      setConflictTarget(null);
      setEditing(row);
      setRefs([]);
      setProbeResult(null);
      setToolSchemas([]);
      // MCP keeps the base on the source, but shows it with the tool arguments.
      const savedArgs = withBaseArgument(
        toArgumentRows(row.tool_arguments ?? {}),
        row.ref,
        row.selector_argument,
      );
      setToolArgs(savedArgs);
      form.resetFields();
      form.setFieldsValue({
        display_name: row.display_name,
        kind: row.kind,
        endpoint: row.endpoint,
        auth_type: row.auth_type || "service",
        api_key_header: row.api_key_header || "x-api-key",
        ref: row.ref,
        top_k: row.top_k,
        min_score: row.min_score,
        timeout_ms: row.timeout_ms,
        allow_insecure_http: row.allow_insecure_http,
        selector_argument: row.selector_argument,
        tool_name: row.tool_name || undefined,
        tool_arguments: row.tool_arguments ?? {},
        shared: row.shared,
      });
      setFormOpen(true);
    },
    [form],
  );

  const submitForm = useCallback(async () => {
    const values = await form.validateFields();
    // For MCP the base is entered as a tool argument; it leaves the argument
    // list for the source's own ref so the stored call carries one selector,
    // declared by the source rather than by whatever the tool happened to name.
    const baseRow = toolArgs.find(
      (row) => _BASE_ARGUMENTS.includes(row.key.trim()) && row.value.trim(),
    );
    const ref = baseRow ? baseRow.value.trim() : (values.ref ?? "").trim();
    if (values.kind === "akb-mcp" && !ref) {
      message.error(t("externalKnowledge.refRequiredFromTool"));
      return;
    }
    const arguments_ = toArgumentRecord(toolArgs);
    if (baseRow) delete arguments_[baseRow.key.trim()];
    const body: ExternalSourceCreateBody = {
      auth_type: ["akb-mcp", "akb-rest"].includes(values.kind)
        ? values.auth_type || "service"
        : "service",
      connection_id: values.connection_id || undefined,
      display_name: values.display_name,
      kind: values.kind,
      endpoint: values.endpoint,
      ref,
      top_k: values.top_k,
      min_score: values.min_score,
      timeout_ms: values.timeout_ms,
      allow_insecure_http: values.allow_insecure_http,
      selector_argument: baseRow
        ? baseRow.key.trim()
        : values.selector_argument ?? "",
      tool_name: values.tool_name ?? "",
      tool_arguments: arguments_,
      shared: values.shared,
    };
    if (values.api_key) body.api_key = values.api_key;
    if (["akb-mcp", "akb-rest"].includes(values.kind) && values.api_key_header)
      body.api_key_header =
        values.auth_type === "agent" ? "x-api-key" : values.api_key_header;
    try {
      let saved: ExternalKnowledgeSource;
      if (editing) {
        const patch: import("@/api/modules/externalKnowledge").ExternalSourcePatchBody =
          {
            ...body,
            revision: editing.revision,
            resource_revision: editing.resource_revision,
          };
        {
          for (const key of [
            "kind",
            "endpoint",
            "api_key",
            "api_key_header",
            "auth_type",
            "allow_insecure_http",
            "shared",
          ] as const)
            delete patch[key];
        }
        delete patch.connection_id;
        patch.resource_shared = values.shared;
        saved = await externalKnowledgeApi.update(editing.source_id, patch);
      } else {
        saved = await externalKnowledgeApi.create(body);
        setEditing(saved);
      }
      setToolArgs(
        rowsForTool(
          toolSchemas.find((item) => item.name === saved.tool_name),
          toolArgs,
        ),
      );
      message.success(t("externalKnowledge.updated"));

      setFormOpen(false);
      await refresh();
    } catch (error) {
      if (parseApiError(error)?.code === "AGENT_IDENTITY_CONFLICT")
        setConflictTarget("resource");
      message.error(
        apiErrorMessage(error, t("externalKnowledge.saveFailed"), t),
      );
    }
  }, [editing, form, message, refresh, t, toolArgs, toolSchemas]);

  const runProbe = useCallback(async () => {
    if (!editing) {
      message.info(t("externalKnowledge.probeAfterSave"));
      return;
    }
    setProbing(true);
    try {
      const result = await externalKnowledgeApi.probe(editing.source_id);
      setProbeResult(result);
      const schemas = result.tool_schemas ?? [];
      setToolSchemas(schemas);
      // A fetched list has to show what it offers even before a tool is picked,
      // so it falls back to the retrieval tool the adapters call by default.
      const tool =
        schemas.find((item) => item.name === selectedTool) ??
        schemas.find((item) => item.name === "knowledge_search") ??
        schemas[0];
      if (!selectedTool && tool) form.setFieldValue("tool_name", tool.name);
      // Refreshing the list should also refresh what a picked tool offers,
      // keeping already filled values and dropping ones the tool dropped.
      setToolArgs((rows) => rowsForTool(tool, rows));
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("externalKnowledge.probeFailedHint"), t),
      );
    } finally {
      setProbing(false);
    }
  }, [editing, form, message, selectedTool, t]);

  const changeTool = useCallback(
    (name: string | undefined) => {
      form.setFieldValue("tool_name", name);
      setToolArgs((rows) =>
        rowsForTool(
          toolSchemas.find((item) => item.name === name),
          rows,
        ),
      );
    },
    [form, toolSchemas],
  );

  const changeToolArgument = useCallback(
    (index: number, patch: Partial<ToolArgumentRow>) => {
      setToolArgs((rows) =>
        rows.map((row, position) =>
          position === index ? { ...row, ...patch } : row,
        ),
      );
    },
    [],
  );

  const addToolArgument = useCallback(() => {
    setToolArgs((rows) => [...rows, { key: "", value: "", required: false }]);
  }, []);

  const removeToolArgument = useCallback((index: number) => {
    setToolArgs((rows) => rows.filter((_, position) => position !== index));
  }, []);

  const fetchRefs = useCallback(async () => {
    if (!editing && !reusedConnection) {
      message.info(t("externalKnowledge.selectConnectionFirst"));
      return;
    }
    setRefsLoading(true);
    try {
      // A binding-managed source refuses the legacy credential calls, so its
      // list has to be fetched through one of the bound Agents instead.
      const viaIdentity = async () => {
        if (!catalogAgent)
          throw new Error(t("externalKnowledge.chooseCatalogAgent"));
        return agentIdentityApi.bases(
          editing?.source_id || `connection:${reusedConnection}`,
          catalogAgent,
        );
      };
      const result =
        (editing?.auth_type || formAuthType) === "agent"
          ? await viaIdentity()
          : editing
          ? await externalKnowledgeApi.refs(editing.source_id)
          : await externalKnowledgeApi.connectionRefs(String(reusedConnection));
      setRefs(result.refs ?? []);
      if ((result.refs ?? []).length === 0) {
        message.info(t("externalKnowledge.noRefs"));
      }
    } catch (e) {
      const unbound =
        e instanceof Error &&
        e.message === t("externalKnowledge.chooseCatalogAgent");
      message.error(
        unbound
          ? t("externalKnowledge.chooseCatalogAgent")
          : t("externalKnowledge.fetchRefsFailed"),
      );
    } finally {
      setRefsLoading(false);
    }
  }, [editing, message, t, catalogAgent, reusedConnection, formAuthType]);

  const toggleEnabled = useCallback(
    async (row: ExternalKnowledgeSource, enabled: boolean) => {
      try {
        await externalKnowledgeApi.update(row.source_id, {
          enabled,
          revision: row.revision,
          resource_revision: row.resource_revision,
        });
        await refresh();
      } catch (error) {
        message.error(
          apiErrorMessage(error, t("externalKnowledge.saveFailed"), t),
        );
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
      } catch (error) {
        message.error(
          apiErrorMessage(error, t("externalKnowledge.deleteFailed"), t),
        );
      }
    },
    [message, refresh, t],
  );

  const probe = useCallback(
    async (row: ExternalKnowledgeSource) => {
      if (row.auth_type === "agent" && row.kind !== "akb-mcp") {
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
      } catch (error) {
        message.error(
          apiErrorMessage(error, t("externalKnowledge.probeFailed"), t),
        );
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
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("externalKnowledge.searchFailed"), t),
      );
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
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("externalKnowledge.bindFailed"), t),
      );
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
      } catch (error) {
        message.error(
          apiErrorMessage(error, t("externalKnowledge.bindFailed"), t),
        );
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
              {!row.has_credentials && (
                <Tooltip title={t("externalKnowledge.noCredentialsHint")}>
                  <Tag color="orange" bordered={false}>
                    {t("externalKnowledge.noCredentials")}
                  </Tag>
                </Tooltip>
              )}
            </div>
            <span className={styles.endpointText}>
              {t("externalKnowledge.parentConnection")}:{" "}
              {connections.find(
                (connection) => connection.connection_id === row.connection_id,
              )?.display_name || row.endpoint}
            </span>
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
        title: t("externalKnowledge.checkStatus"),
        key: "checks",
        render: (_value: unknown, row) => (
          <Space wrap>
            {(row.checks || []).length ? (
              row.checks?.map((check) => (
                <Tag
                  key={`${check.agent_id}:${check.stage}`}
                  color={check.status === "success" ? "green" : "red"}
                >
                  <Tooltip
                    title={`${formatServerIsoDateTime(
                      new Date(check.checked_at * 1000).toISOString(),
                      timezone,
                    )} · ${check.code ? `${check.code} · ` : ""}${checkHint(
                      check.code,
                      t,
                      check.status,
                    )}`}
                  >
                    {agents.find((a) => a.agent_id === check.agent_id)?.name ||
                      (check.agent_id
                        ? t("externalKnowledge.unknownAgent")
                        : t("externalKnowledge.connection"))}{" "}
                    ·{" "}
                    {t(`externalKnowledge.stages.${check.stage}`, {
                      defaultValue: check.stage,
                    })}{" "}
                    ·{" "}
                    {t(
                      check.status === "success"
                        ? "externalKnowledge.checkPassed"
                        : "externalKnowledge.checkFailed",
                    )}
                  </Tooltip>
                </Tag>
              ))
            ) : (
              <Tag>{t("externalKnowledge.notVerified")}</Tag>
            )}
          </Space>
        ),
      },
      {
        title: t("externalKnowledge.authType"),
        key: "auth_type",
        render: (_value: unknown, row) =>
          t(
            row.needs_attention
              ? "externalKnowledge.needsAttention"
              : row.auth_type === "agent"
              ? "externalKnowledge.authAgent"
              : "externalKnowledge.authService",
          ),
      },
      {
        title: t("externalKnowledge.actions"),
        key: "actions",
        width: 340,
        render: (_value: unknown, row) => (
          <div className={styles.rowActions}>
            {row.auth_type === "agent" && (
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
                  if (row.auth_type === "agent") {
                    setIdentitySource(row);
                    return;
                  }
                  setTesting(row);
                  setTestChunks([]);
                  setTestQuery("");
                }}
              />
            </Tooltip>
            {/* Identity bindings verify Agent credentials; service sources
                use the ordinary source availability bindings. */}
            {
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
            }
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
    [
      agents,
      connections,
      timezone,
      openEdit,
      probe,
      removeSource,
      t,
      toggleEnabled,
      unbind,
    ],
  );

  async function reviewLatest() {
    try {
      const current =
        conflictTarget === "connection"
          ? (await externalKnowledgeApi.connections()).find(
              (row) =>
                row.connection_id ===
                (connectionEditor !== "new" && connectionEditor?.connection_id),
            )
          : (await externalKnowledgeApi.list()).find(
              (row) => row.source_id === editing?.source_id,
            );
      if (!current) {
        message.error(t("externalKnowledge.loadFailed"));
        return;
      }
      const safeFields = Object.fromEntries(
        Object.entries(current).filter(([key]) =>
          [
            "display_name",
            "endpoint",
            "auth_type",
            "ref",
            "tool_name",
            "top_k",
            "shared",
            "enabled",
            "revision",
            "resource_revision",
          ].includes(key),
        ),
      );
      modal.confirm({
        title: t("externalKnowledge.reviewLatest"),
        content: (
          <Space direction="vertical">
            <Typography.Text>
              {t("externalKnowledge.keepInputHelp")}
            </Typography.Text>
            <pre style={{ whiteSpace: "pre-wrap" }}>
              {JSON.stringify(safeFields, null, 2)}
            </pre>
          </Space>
        ),
        onOk: () => {
          if (conflictTarget === "connection")
            setConnectionEditor(current as ExternalKnowledgeConnection);
          else setEditing(current as ExternalKnowledgeSource);
          setConflictTarget(null);
        },
      });
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("externalKnowledge.loadFailed"), t),
      );
    }
  }

  return (
    <PageShell
      title={t("externalKnowledge.title")}
      subtitle={t("externalKnowledge.description")}
      fill={!isMobile}
      actions={
        section === "connections" ? (
          <Button
            type="primary"
            icon={<Plus size={16} />}
            onClick={openNewConnection}
          >
            {t("externalKnowledge.newConnection")}
          </Button>
        ) : (
          <Button
            type="primary"
            icon={<Plus size={16} />}
            disabled={
              !connections.some((row) => row.enabled && !row.needs_attention)
            }
            onClick={openCreate}
          >
            {t("externalKnowledge.new")}
          </Button>
        )
      }
    >
      <div className={styles.layout}>
        <div className={styles.setupGuide}>
          <Typography.Title level={5}>
            {t("externalKnowledge.setupTitle")}
          </Typography.Title>
          <p>{t("externalKnowledge.setupHelp")}</p>
          <ol>
            <li>{t("externalKnowledge.setupConnection")}</li>
            <li>{t("externalKnowledge.setupIdentity")}</li>
            <li>{t("externalKnowledge.setupResource")}</li>
          </ol>
        </div>
        <Tabs
          className={styles.sectionTabs}
          activeKey={section}
          onChange={setSection}
          items={[
            {
              key: "connections",
              label: `${t("externalKnowledge.connection")} (${
                connections.length
              })`,
            },
            {
              key: "resources",
              label: `${t("externalKnowledge.resources")} (${sources.length})`,
            },
          ]}
        />
        <section
          className={styles.connectionSection}
          hidden={section !== "connections"}
        >
          <div className={styles.sectionHeading}>
            <div>
              <Typography.Title level={5}>
                {t("externalKnowledge.connection")}
              </Typography.Title>
              <Typography.Text type="secondary">
                {t("externalKnowledge.connectionDefinition")}
              </Typography.Text>
            </div>
          </div>
          <Table<ExternalKnowledgeConnection>
            size="small"
            rowKey="connection_id"
            dataSource={connections}
            pagination={false}
            scroll={{ x: 650 }}
            columns={[
              { title: t("externalKnowledge.name"), dataIndex: "display_name" },
              {
                title: t("externalKnowledge.endpoint"),
                dataIndex: "endpoint",
                ellipsis: true,
              },
              {
                title: t("externalKnowledge.authType"),
                render: (_, row) =>
                  t(
                    row.needs_attention
                      ? "externalKnowledge.needsAttention"
                      : row.auth_type === "agent"
                      ? "externalKnowledge.authAgent"
                      : "externalKnowledge.authService",
                  ),
              },
              {
                title: t("externalKnowledge.checkStatus"),
                render: (_, row) => {
                  const check = row.check
                    ? { ...row.check, ok: row.check.status === "success" }
                    : null;
                  return (
                    <Space direction="vertical" size={2}>
                      <Tag
                        color={
                          !row.has_credentials
                            ? "red"
                            : check
                            ? check.ok
                              ? "green"
                              : "red"
                            : "default"
                        }
                      >
                        {!row.has_credentials
                          ? t("externalKnowledge.missingServiceKey")
                          : check
                          ? t(
                              check.ok
                                ? "externalKnowledge.checkPassed"
                                : "externalKnowledge.checkFailed",
                            )
                          : t("externalKnowledge.notVerified")}
                      </Tag>
                      {check && (
                        <Typography.Text type="secondary">
                          {checkHint(
                            check.code,
                            t,
                            check.ok ? "success" : "failed",
                          )}
                        </Typography.Text>
                      )}
                    </Space>
                  );
                },
              },
              {
                title: t("externalKnowledge.resourceCount"),
                dataIndex: "resource_count",
              },
              {
                title: t("externalKnowledge.actions"),
                render: (_, row) => (
                  <Space wrap>
                    <Button
                      size="small"
                      icon={<Activity size={14} />}
                      loading={checkingConnection === row.connection_id}
                      disabled={
                        !row.enabled ||
                        !["akb-mcp", "akb-rest"].includes(row.kind)
                      }
                      onClick={() => void checkConnection(row)}
                    >
                      {t("externalKnowledge.checkConnection")}
                    </Button>
                    <Button
                      size="small"
                      onClick={() => setDetailConnectionId(row.connection_id)}
                    >
                      {t("externalKnowledge.details")}
                    </Button>
                    {row.can_manage && (
                      <Space>
                        <Switch
                          checked={!!row.enabled}
                          onChange={async (enabled) => {
                            try {
                              if (!(await confirmImpact(row))) return;
                              await externalKnowledgeApi.updateConnection(
                                row.connection_id,
                                { revision: row.revision, enabled },
                              );
                              await refresh();
                            } catch (error) {
                              message.error(
                                apiErrorMessage(
                                  error,
                                  t("externalKnowledge.saveFailed"),
                                  t,
                                ),
                              );
                            }
                          }}
                        />
                        <Button
                          onClick={() => {
                            connectionForm.resetFields();
                            connectionForm.setFieldsValue({
                              ...row,
                              auth_type: row.needs_attention
                                ? undefined
                                : row.auth_type,
                              api_key: undefined,
                            });
                            setConnectionEditor(row);
                          }}
                        >
                          {t("externalKnowledge.edit")}
                        </Button>
                        <Popconfirm
                          title={t("externalKnowledge.delete")}
                          description={t(
                            "externalKnowledge.deleteConnectionHelp",
                            { agents: row.agent_count },
                          )}
                          onConfirm={async () => {
                            try {
                              await externalKnowledgeApi.deleteConnection(
                                row.connection_id,
                              );
                              await refresh();
                            } catch (error) {
                              message.error(
                                apiErrorMessage(
                                  error,
                                  t("externalKnowledge.saveFailed"),
                                  t,
                                ),
                              );
                            }
                          }}
                        >
                          <Button danger disabled={row.resource_count > 0}>
                            {t("externalKnowledge.delete")}
                          </Button>
                        </Popconfirm>
                      </Space>
                    )}
                  </Space>
                ),
              },
            ]}
          />
        </section>
        <section
          className={styles.resourceSection}
          hidden={section !== "resources"}
        >
          <div className={styles.sectionHeading}>
            <div>
              <Typography.Title level={5}>
                {t("externalKnowledge.resources")}
              </Typography.Title>
              <Typography.Text type="secondary">
                {t("externalKnowledge.resourceDefinition")}
              </Typography.Text>
            </div>
          </div>
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
        </section>
      </div>

      <Drawer
        open={!!detailConnection}
        width={900}
        title={detailConnection?.display_name}
        onClose={() => setDetailConnectionId(undefined)}
      >
        {detailConnection && (
          <Tabs
            items={[
              {
                key: "connection",
                label: t("externalKnowledge.connection"),
                children: (
                  <Space direction="vertical" style={{ width: "100%" }}>
                    <Typography.Text>
                      {detailConnection.endpoint}
                    </Typography.Text>
                    <Alert
                      type={
                        detailConnection.needs_attention ? "warning" : "info"
                      }
                      message={t(
                        detailConnection.needs_attention
                          ? "externalKnowledge.needsAttention"
                          : detailConnection.auth_type === "agent"
                          ? "externalKnowledge.authAgentHelp"
                          : "externalKnowledge.authServiceHelp",
                      )}
                    />
                    {detailConnection.can_manage && (
                      <Button
                        onClick={() => {
                          connectionForm.resetFields();
                          connectionForm.setFieldsValue({
                            ...detailConnection,
                            auth_type: detailConnection.needs_attention
                              ? undefined
                              : detailConnection.auth_type,
                            api_key: undefined,
                          });
                          setConnectionEditor(detailConnection);
                        }}
                      >
                        {t("externalKnowledge.edit")}
                      </Button>
                    )}
                  </Space>
                ),
              },
              {
                key: "resources",
                label: t("externalKnowledge.resources"),
                children: (
                  <Space direction="vertical" style={{ width: "100%" }}>
                    <Button
                      onClick={() => {
                        openCreate();
                        form.setFieldsValue({
                          connection_id: detailConnection.connection_id,
                          kind: detailConnection.kind,
                          endpoint: detailConnection.endpoint,
                          auth_type:
                            detailConnection.auth_type === "legacy"
                              ? undefined
                              : detailConnection.auth_type,
                        });
                      }}
                    >
                      {t("externalKnowledge.new")}
                    </Button>
                    <Table
                      rowKey="source_id"
                      pagination={false}
                      dataSource={detailResources}
                      columns={[
                        {
                          title: t("externalKnowledge.name"),
                          dataIndex: "display_name",
                        },
                        { title: t("externalKnowledge.ref"), dataIndex: "ref" },
                        {
                          title: t("externalKnowledge.actions"),
                          render: (_, row) => (
                            <Space>
                              <Button
                                disabled={!row.can_manage}
                                onClick={() => openEdit(row)}
                              >
                                {t("externalKnowledge.edit")}
                              </Button>
                              <Button onClick={() => setBindingSource(row)}>
                                {t("externalKnowledge.bind")}
                              </Button>
                              <Button
                                onClick={() => {
                                  if (row.auth_type === "agent")
                                    setIdentitySource(row);
                                  else {
                                    setTesting(row);
                                    setTestQuery("");
                                    setTestChunks([]);
                                  }
                                }}
                              >
                                {t("externalKnowledge.test")}
                              </Button>
                            </Space>
                          ),
                        },
                      ]}
                    />
                  </Space>
                ),
              },
              {
                key: "identities",
                label: t("externalKnowledge.identity.manage"),
                children: (
                  <Space direction="vertical">
                    <Alert
                      type="info"
                      message={t("externalKnowledge.identityConnectionHelp")}
                    />
                    <Button
                      disabled={detailConnection.auth_type !== "agent"}
                      onClick={() =>
                        setIdentitySource({
                          source_id:
                            "connection:" + detailConnection.connection_id,
                          connection_id: detailConnection.connection_id,
                          display_name: detailConnection.display_name,
                        })
                      }
                    >
                      {t("externalKnowledge.identity.manage")}
                    </Button>
                  </Space>
                ),
              },
            ]}
          />
        )}
      </Drawer>

      <SimpleAgentBindingDrawer
        source={identitySource}
        connectionName={
          connections.find(
            (row) => row.connection_id === identitySource?.connection_id,
          )?.display_name
        }
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
        open={connectionEditor !== null}
        width={520}
        title={t("externalKnowledge.connection")}
        onClose={() => setConnectionEditor(null)}
        extra={
          <Button
            type="primary"
            onClick={async () => {
              try {
                const values = await connectionForm.validateFields();
                if (
                  connectionEditor &&
                  connectionEditor !== "new" &&
                  !(await confirmImpact(connectionEditor))
                )
                  return;
                if (connectionEditor === "new")
                  await externalKnowledgeApi.createConnection(values);
                else if (connectionEditor)
                  await externalKnowledgeApi.updateConnection(
                    connectionEditor.connection_id,
                    { ...values, revision: connectionEditor.revision },
                  );
                setConnectionEditor(null);
                await refresh();
                message.success(t("externalKnowledge.updated"));
              } catch (error) {
                if (parseApiError(error)?.code === "AGENT_IDENTITY_CONFLICT")
                  setConflictTarget("connection");
                message.error(
                  apiErrorMessage(error, t("externalKnowledge.saveFailed"), t),
                );
              }
            }}
          >
            {t("externalKnowledge.save")}
          </Button>
        }
      >
        {conflictTarget === "connection" && (
          <Alert
            type="warning"
            showIcon
            message={t("externalKnowledge.conflictHelp")}
            action={
              <Button onClick={() => void reviewLatest()}>
                {t("externalKnowledge.reviewLatest")}
              </Button>
            }
          />
        )}
        <Form form={connectionForm} layout="vertical">
          <Form.Item
            name="display_name"
            label={t("externalKnowledge.name")}
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="kind" label={t("externalKnowledge.kind")}>
            <Select
              disabled={
                connectionEditor !== "new" && !!connectionEditor?.agent_count
              }
              options={kinds.map((kind) => ({ value: kind, label: kind }))}
              onChange={(kind: string) => {
                if (!["akb-mcp", "akb-rest"].includes(kind))
                  connectionForm.setFieldValue("auth_type", "service");
              }}
            />
          </Form.Item>
          <Form.Item
            name="endpoint"
            label={t("externalKnowledge.endpoint")}
            rules={[{ required: true }]}
          >
            <Input
              disabled={
                connectionEditor !== "new" && !!connectionEditor?.agent_count
              }
            />
          </Form.Item>
          <Form.Item
            name="auth_type"
            label={t("externalKnowledge.authType")}
            rules={[{ required: true }]}
          >
            <Select
              disabled={
                connectionEditor !== "new" && !!connectionEditor?.agent_count
              }
              onChange={(mode: string) => {
                if (mode === "agent")
                  connectionForm.setFieldValue("api_key_header", "x-api-key");
              }}
              options={[
                { value: "service", label: t("externalKnowledge.authService") },
                ...(["akb-mcp", "akb-rest"].includes(connectionKind)
                  ? [
                      {
                        value: "agent",
                        label: t("externalKnowledge.authAgent"),
                      },
                    ]
                  : []),
              ]}
            />
          </Form.Item>
          <Form.Item
            name="api_key"
            label={t("externalKnowledge.apiKey")}
            extra={t("externalKnowledge.apiKeyHint")}
            rules={[
              {
                required:
                  ["akb-mcp", "akb-rest"].includes(connectionKind) &&
                  (connectionEditor === "new" ||
                    !connectionEditor?.has_credentials),
                message: t("externalKnowledge.missingServiceKey"),
              },
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          {["akb-mcp", "akb-rest"].includes(connectionKind) && (
            <Form.Item
              name="api_key_header"
              label={t("externalKnowledge.authHeader")}
              extra={t("externalKnowledge.headerHelp")}
            >
              <Select
                disabled={connectionAuth === "agent"}
                options={[
                  { value: "x-api-key", label: "X-API-Key" },
                  { value: "authorization", label: "Authorization: Bearer" },
                ]}
              />
            </Form.Item>
          )}
          <Form.Item
            name="shared"
            valuePropName="checked"
            label={t("externalKnowledge.shared")}
          >
            <Switch />
          </Form.Item>
          <Form.Item
            name="allow_insecure_http"
            valuePropName="checked"
            label={t("externalKnowledge.insecureWarning")}
          >
            <Switch />
          </Form.Item>
        </Form>
      </Drawer>
      {/* Everything the probe learned that is not a tool: reachable only from
          the info icon, so it never competes with the form for space. */}
      <Modal
        title={t("externalKnowledge.probeDetailsTitle")}
        open={probeInfoOpen}
        onCancel={() => setProbeInfoOpen(false)}
        footer={null}
        width={560}
      >
        {probeResult && (
          <Space direction="vertical" size={8} style={{ width: "100%" }}>
            <Space size={4} wrap>
              <Tag color={probeResult.ok ? "green" : "red"}>
                {probeResult.ok
                  ? t("externalKnowledge.probeOk")
                  : t("externalKnowledge.probeFailed")}
              </Tag>
              {probeResult.message && (
                <Typography.Text type="secondary">
                  {probeResult.message}
                </Typography.Text>
              )}
            </Space>
            {probeResult.ok && editing?.auth_type === "agent" && (
              <Alert
                type="info"
                showIcon
                description={t("externalKnowledge.authAgentHelp")}
              />
            )}
            {probeResult.protocol_version && (
              <Space size={4} wrap>
                <Typography.Text type="secondary">
                  {t("externalKnowledge.protocol")}
                </Typography.Text>
                <Tag>{probeResult.protocol_version}</Tag>
              </Space>
            )}
            {probeResult.server_info &&
              Object.keys(probeResult.server_info).length > 0 && (
                <Space size={4} wrap>
                  <Typography.Text type="secondary">
                    {t("externalKnowledge.server")}
                  </Typography.Text>
                  <Tag>
                    {String(probeResult.server_info.name ?? "")}
                    {probeResult.server_info.version
                      ? ` v${probeResult.server_info.version}`
                      : ""}
                  </Tag>
                </Space>
              )}
            {probeResult.capabilities &&
              Object.keys(probeResult.capabilities).length > 0 && (
                <Space size={4} wrap>
                  <Typography.Text type="secondary">
                    {t("externalKnowledge.capabilities")}
                  </Typography.Text>
                  {Object.entries(probeResult.capabilities).map(
                    ([key, value]) => (
                      <Tag key={key}>
                        {key}
                        {value && typeof value === "object"
                          ? `: ${Object.keys(
                              value as Record<string, unknown>,
                            ).join(", ")}`
                          : ""}
                      </Tag>
                    ),
                  )}
                </Space>
              )}
            {probeResult.tools.length ? (
              <Space size={4} wrap>
                {probeResult.tools.map((tool) => (
                  <Typography.Text
                    key={tool}
                    copyable={{ text: tool, tooltips: false }}
                  >
                    <Tag>{tool}</Tag>
                  </Typography.Text>
                ))}
              </Space>
            ) : (
              probeResult.ok && (
                <Typography.Text type="secondary">
                  {t("externalKnowledge.toolsEmpty")}
                </Typography.Text>
              )
            )}
            {probeResult.instructions && (
              <>
                <Typography.Text type="secondary">
                  {t("externalKnowledge.instructions")}
                </Typography.Text>
                <Typography.Paragraph
                  copyable={{
                    text: probeResult.instructions,
                    tooltips: false,
                  }}
                  ellipsis={{
                    rows: 6,
                    expandable: true,
                    symbol: t("externalKnowledge.instructionsMore"),
                  }}
                  style={{ marginBottom: 0 }}
                >
                  {probeResult.instructions}
                </Typography.Paragraph>
              </>
            )}
          </Space>
        )}
      </Modal>
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
        {conflictTarget === "resource" && (
          <Alert
            type="warning"
            showIcon
            message={t("externalKnowledge.conflictHelp")}
            action={
              <Button onClick={() => void reviewLatest()}>
                {t("externalKnowledge.reviewLatest")}
              </Button>
            }
          />
        )}
        <Form form={form} layout="vertical" className={styles.form}>
          <div className={styles.sectionTitle}>
            {t("externalKnowledge.basicSection")}
          </div>
          {!editing && (
            <Form.Item
              name="connection_id"
              label={t("externalKnowledge.connection")}
              extra={t("externalKnowledge.connectionHelp")}
              rules={[
                {
                  required: true,
                  message: t("externalKnowledge.selectConnectionFirst"),
                },
              ]}
            >
              <Select
                options={[
                  ...connections
                    .filter((row) => row.enabled)
                    .map((row) => ({
                      value: row.connection_id,
                      label: `${row.display_name} · ${row.endpoint}`,
                    })),
                ]}
                onChange={(value: string) => {
                  setRefs([]);
                  form.setFieldValue("ref", "");
                  const row = connections.find(
                    (item) => item.connection_id === value,
                  );
                  if (row)
                    form.setFieldsValue({
                      kind: row.kind,
                      endpoint: row.endpoint,
                      auth_type:
                        row.auth_type === "legacy"
                          ? undefined
                          : row.auth_type || "service",
                      api_key: undefined,
                      api_key_header: "x-api-key",
                      allow_insecure_http: row.allow_insecure_http,
                    });
                }}
              />
            </Form.Item>
          )}
          <Form.Item
            name="display_name"
            label={t("externalKnowledge.name")}
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
          {formKind === "akb-mcp" && (
            <Form.Item
              label={t("externalKnowledge.tools")}
              extra={t("externalKnowledge.toolsFetchHint")}
            >
              <Space.Compact>
                <Button
                  loading={probing}
                  disabled={!editing}
                  onClick={() => void runProbe()}
                  title={editing ? "" : t("externalKnowledge.probeAfterSave")}
                >
                  {t("externalKnowledge.fetchTools")}
                </Button>
                {/* Everything the probe learned that is not a tool is
                    diagnostic detail: it stays behind an icon so the form keeps
                    its width for what the operator has to fill in. */}
                {probeResult && (
                  <Tooltip title={t("externalKnowledge.probeDetails")}>
                    <Button
                      aria-label={t("externalKnowledge.probeDetails")}
                      icon={<Info size={14} />}
                      onClick={() => setProbeInfoOpen(true)}
                    />
                  </Tooltip>
                )}
              </Space.Compact>
            </Form.Item>
          )}
          {!!editing && (
            <Alert
              type="info"
              showIcon
              message={t("externalKnowledge.resourceOnlyHelp")}
              action={
                <Button
                  onClick={() => setDetailConnectionId(editing.connection_id)}
                >
                  {t("externalKnowledge.details")}
                </Button>
              }
            />
          )}
          <div hidden={!!editing || !!reusedConnection}>
            <Form.Item name="kind" label={t("externalKnowledge.kind")}>
              <Select
                disabled={
                  !!reusedConnection ||
                  editing?.can_manage_connection === false ||
                  editing?.identity_configured
                }
                onChange={(kind: string) => {
                  if (!["akb-mcp", "akb-rest"].includes(kind))
                    form.setFieldValue("auth_type", "service");
                }}
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
                disabled={
                  !!reusedConnection ||
                  editing?.can_manage_connection === false ||
                  editing?.identity_configured
                }
                placeholder={
                  formKind === "standard-retrieval"
                    ? "https://akb.example.com/api/v1/external/kb/retrievals"
                    : "https://akb.example.com"
                }
              />
            </Form.Item>
            <Form.Item
              name="auth_type"
              rules={[{ required: true }]}
              label={t("externalKnowledge.authType")}
              extra={t(
                formAuthType === "agent"
                  ? "externalKnowledge.authAgentHelp"
                  : "externalKnowledge.authServiceHelp",
              )}
            >
              <Select
                disabled={
                  !!reusedConnection ||
                  editing?.can_manage_connection === false ||
                  editing?.identity_configured
                }
                options={[
                  {
                    value: "service",
                    label: t("externalKnowledge.authService"),
                  },
                  ...(["akb-mcp", "akb-rest"].includes(formKind)
                    ? [
                        {
                          value: "agent",
                          label: t("externalKnowledge.authAgent"),
                        },
                      ]
                    : []),
                ]}
              />
            </Form.Item>
            <Form.Item name="api_key" label={t("externalKnowledge.apiKey")}>
              <Input.Password
                disabled={
                  !!reusedConnection || editing?.can_manage_connection === false
                }
                autoComplete="new-password"
                placeholder={editing ? t("externalKnowledge.apiKeyHint") : ""}
              />
            </Form.Item>
            {["akb-mcp", "akb-rest"].includes(formKind) &&
              formAuthType !== "agent" && (
                <Form.Item
                  name="api_key_header"
                  label={t("externalKnowledge.apiKeyHeader")}
                  extra={t("externalKnowledge.apiKeyHeaderHint")}
                >
                  <Select
                    options={[
                      { value: "x-api-key", label: "X-API-Key" },
                      {
                        value: "authorization",
                        label: "Authorization: Bearer",
                      },
                    ]}
                  />
                </Form.Item>
              )}
          </div>
          {/* Every adapter addresses one knowledge base, MCP included: how the
              call travels differs, what it selects does not — knowledge_query
              without a base is rejected by AKB. */}
          {formKind !== "akb-mcp" && (
            <Form.Item
              label={t("externalKnowledge.ref")}
              extra={
                formKind === "akb-mcp"
                  ? t("externalKnowledge.refHintMcp")
                  : t("externalKnowledge.refHint")
              }
              rules={
                formKind === "akb-mcp"
                  ? [
                      {
                        required: true,
                        message: t("externalKnowledge.refRequired"),
                      },
                    ]
                  : undefined
              }
            >
              <Space.Compact style={{ width: "100%" }}>
                {/* Any value goes through: a stable code, a numeric id, whatever
                  the selector argument names. The fetched list only suggests. */}
                <Form.Item
                  name="ref"
                  noStyle
                  rules={
                    formKind.startsWith("akb-")
                      ? [
                          {
                            required: true,
                            pattern: /^KB_[A-Za-z0-9]{1,125}$/,
                            message: t("externalKnowledge.refHint"),
                          },
                        ]
                      : undefined
                  }
                >
                  <AutoComplete
                    style={{ flex: 1 }}
                    filterOption
                    aria-label={t("externalKnowledge.ref")}
                    placeholder={
                      formKind.startsWith("akb-")
                        ? "KB_…"
                        : t("externalKnowledge.ref")
                    }
                    options={refs.map((item) => ({
                      value: item.ref,
                      label: item.name
                        ? `${item.name}（${item.ref}）`
                        : item.ref,
                    }))}
                  />
                </Form.Item>
                {(editing?.auth_type || formAuthType) === "agent" && (
                  <Select
                    aria-label={t("externalKnowledge.catalogAgent")}
                    value={catalogAgent}
                    onChange={setCatalogAgent}
                    options={catalogAgents}
                    style={{ minWidth: 140 }}
                    placeholder={t("externalKnowledge.catalogAgent")}
                  />
                )}
                <Button
                  loading={refsLoading}
                  disabled={
                    (!editing && !reusedConnection) ||
                    ((editing?.auth_type || formAuthType) === "agent" &&
                      !catalogAgent)
                  }
                  onClick={() => void fetchRefs()}
                  title={
                    editing || reusedConnection
                      ? ""
                      : t("externalKnowledge.selectConnectionFirst")
                  }
                >
                  {t("externalKnowledge.fetchRefs")}
                </Button>
              </Space.Compact>
            </Form.Item>
          )}
          {formKind === "akb-mcp" && (
            <>
              <div className={styles.sectionTitle}>
                {t("externalKnowledge.mcpSection")}
              </div>
              {/* The fetched tools are listed, not tucked into a dropdown: the
                  operator picks one and sets what that tool declares. */}
              <Form.Item
                label={t("externalKnowledge.toolList")}
                extra={t("externalKnowledge.toolListHint")}
              >
                {toolOptions.length === 0 ? (
                  <Typography.Text type="secondary">
                    {t("externalKnowledge.toolListEmpty")}
                  </Typography.Text>
                ) : (
                  <Space
                    direction="vertical"
                    size={4}
                    style={{ width: "100%" }}
                  >
                    {toolOptions.map((tool) => {
                      const active = selectedTool === tool.name;
                      return (
                        <Button
                          key={tool.name}
                          block
                          type={active ? "primary" : "default"}
                          aria-pressed={active}
                          style={{
                            height: "auto",
                            textAlign: "left",
                            whiteSpace: "normal",
                          }}
                          onClick={() => changeTool(tool.name)}
                        >
                          {toolLabel(tool)}
                        </Button>
                      );
                    })}
                  </Space>
                )}
              </Form.Item>
              <Form.Item
                label={t("externalKnowledge.toolArguments")}
                extra={t("externalKnowledge.toolArgumentsHint")}
              >
                <Space direction="vertical" size={8} style={{ width: "100%" }}>
                  {toolArgs.map((row, index) => {
                    const duplicated =
                      row.key.trim() !== "" &&
                      toolArgs.some(
                        (other, position) =>
                          position !== index &&
                          other.key.trim() === row.key.trim(),
                      );
                    return (
                      <Space.Compact
                        // Edited rows carry the argument name, so the index is
                        // the only stable identity while typing into them.
                        key={`${row.key}-${index}`}
                        style={{ width: "100%" }}
                      >
                        <Input
                          style={{ flex: 1 }}
                          disabled={row.required}
                          status={duplicated ? "error" : undefined}
                          placeholder={t("externalKnowledge.toolArgumentKey")}
                          value={row.key}
                          onChange={(event) =>
                            changeToolArgument(index, {
                              key: event.target.value,
                            })
                          }
                        />
                        <Input
                          style={{ flex: 2 }}
                          placeholder={t("externalKnowledge.toolArgumentValue")}
                          value={row.value}
                          onChange={(event) =>
                            changeToolArgument(index, {
                              value: event.target.value,
                            })
                          }
                        />
                        <Button
                          danger
                          aria-label={t("externalKnowledge.toolArgumentRemove")}
                          title={t("externalKnowledge.toolArgumentRemove")}
                          onClick={() => removeToolArgument(index)}
                        >
                          <Trash2 size={14} />
                        </Button>
                      </Space.Compact>
                    );
                  })}
                  {toolArgs.length === 0 && (
                    <Typography.Text type="secondary">
                      {t("externalKnowledge.toolArgumentsEmpty")}
                    </Typography.Text>
                  )}
                  <Button
                    icon={<Plus size={14} />}
                    disabled={!editing}
                    onClick={addToolArgument}
                  >
                    {t("externalKnowledge.toolArgumentAdd")}
                  </Button>
                </Space>
              </Form.Item>
            </>
          )}

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
            <Input
              placeholder="knowledgeBaseId"
              disabled={formKind.startsWith("akb-")}
            />
          </Form.Item>

          <div hidden={!!editing || !!reusedConnection}>
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
          </div>
          {!!editing && (
            <Form.Item
              name="shared"
              valuePropName="checked"
              label={t("externalKnowledge.resourceShared")}
              extra={t("externalKnowledge.resourceSharedHelp")}
            >
              <Switch />
            </Form.Item>
          )}
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
