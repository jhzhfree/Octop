import { Alert, Button, Modal, Space, Typography } from "antd";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  externalKnowledgeApi,
  type ExternalKnowledgeSource,
} from "../../api/modules/externalKnowledge";
import type { DigitalWorker } from "../../api/modules/organizations";
import {
  workerKnowledgeMappingsApi as api,
  type MappingCreate,
  type WorkerKnowledgeMapping,
} from "../../api/modules/workerKnowledgeMappings";
import { apiErrorMessage, parseApiError } from "../../utils/apiError";
import {
  mappingCapabilities,
  mappingSourceOptions,
} from "./knowledgeMappingOptions";

interface Props {
  organizationId: string;
  worker: DigitalWorker;
  canManage: boolean;
}
/** Local draft management only. This panel never invokes retrieval or changes grants. */
export default function KnowledgeMappings({
  organizationId,
  worker,
  canManage,
}: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<WorkerKnowledgeMapping[]>([]);
  const [sources, setSources] = useState<ExternalKnowledgeSource[]>([]);
  const [offset, setOffset] = useState(0);
  const [capIndex, setCapIndex] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [sourceSearch, setSourceSearch] = useState("");
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<
    { create: MappingCreate } | { revoke: WorkerKnowledgeMapping } | null
  >(null);
  const generation = useRef(0);
  const lock = useRef(false);
  const capabilities = mappingCapabilities(worker.capabilities);
  const cap = capIndex ? capabilities[Number(capIndex) - 1] : undefined;
  const candidates = cap
    ? mappingSourceOptions(sources, worker.agent_id, cap.knowledge_base_ref)
    : [];
  const source = candidates.find((s) => s.source_id === sourceId);
  useEffect(() => {
    generation.current += 1;
    lock.current = false;
    setOpen(false);
    setPending(null);
    setItems([]);
    setSources([]);
    setLoaded(false);
    setBusy(false);
    return () => {
      generation.current += 1;
    };
  }, [
    organizationId,
    worker.worker_id,
    worker.version,
    worker.capability_version,
    canManage,
  ]);
  function close() {
    generation.current += 1;
    lock.current = false;
    setOpen(false);
    setPending(null);
    setItems([]);
    setSources([]);
    setLoaded(false);
    setBusy(false);
    setCapIndex("");
    setSourceId("");
    setError("");
  }
  function fail(e: unknown) {
    setPending(null);
    setLoaded(false);
    setCapIndex("");
    setSourceId("");
    const code = parseApiError(e)?.code;
    setError(
      code === "DIGITAL_WORKER_CAPABILITY_VERSION_CONFLICT"
        ? t("knowledgeMappings.conflict")
        : apiErrorMessage(e, t("knowledgeMappings.failed"), t),
    );
  }
  async function load(page = 0) {
    if (!canManage || lock.current) return;
    lock.current = true;
    setBusy(true);
    setLoaded(false);
    setError("");
    setPending(null);
    setCapIndex("");
    setSourceId("");
    const gen = generation.current;
    try {
      const [result, visible] = await Promise.all([
        api.list(organizationId, worker.worker_id, page),
        externalKnowledgeApi.list(),
      ]);
      if (gen !== generation.current) return;
      setItems(result.items);
      setSources(visible);
      setOffset(page);
      setLoaded(true);
    } catch (e) {
      if (gen === generation.current) fail(e);
    } finally {
      if (gen === generation.current) {
        lock.current = false;
        setBusy(false);
      }
    }
  }
  async function confirm() {
    if (!pending || !loaded || lock.current || !canManage) return;
    lock.current = true;
    setBusy(true);
    setError("");
    const gen = generation.current;
    try {
      if ("create" in pending)
        await api.create(organizationId, worker.worker_id, pending.create);
      else
        await api.revoke(
          organizationId,
          worker.worker_id,
          pending.revoke.mapping_id,
          pending.revoke.revision,
        );
      if (gen !== generation.current) return;
      setPending(null);
      lock.current = false;
      await load(0);
    } catch (e) {
      if (gen === generation.current) fail(e);
    } finally {
      if (gen === generation.current) {
        lock.current = false;
        setBusy(false);
      }
    }
  }
  async function inspect(row: WorkerKnowledgeMapping) {
    if (lock.current || !loaded) return;
    lock.current = true;
    setBusy(true);
    const gen = generation.current;
    try {
      const result = await api.inspect(
        organizationId,
        worker.worker_id,
        row.mapping_id,
      );
      if (gen === generation.current)
        setItems((old) =>
          old.map((item) =>
            item.mapping_id === result.mapping_id ? result : item,
          ),
        );
    } catch (e) {
      if (gen === generation.current) fail(e);
    } finally {
      if (gen === generation.current) {
        lock.current = false;
        setBusy(false);
      }
    }
  }
  if (!canManage) return null;
  return (
    <>
      <Button
        onClick={() => {
          setOpen(true);
          void load(0);
        }}
      >
        {t("knowledgeMappings.title")}
      </Button>
      <Modal
        open={open}
        title={t("knowledgeMappings.title")}
        onCancel={close}
        footer={<Button onClick={close}>{t("knowledgeMappings.close")}</Button>}
        width={800}
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Alert
            type="warning"
            showIcon
            message={t("knowledgeMappings.boundary")}
          />
          <Typography.Paragraph>
            {t("knowledgeMappings.sourceLimit")}
          </Typography.Paragraph>
          {error && <Alert type="error" role="alert" message={error} />}
          <Button disabled={busy} onClick={() => void load(offset)}>
            {t("knowledgeMappings.refresh")}
          </Button>
          {!loaded && !busy && (
            <Typography.Text>
              {t("knowledgeMappings.notLoaded")}
            </Typography.Text>
          )}
          {loaded && (
            <>
              <label>
                {t("knowledgeMappings.capability")}
                <select
                  aria-label={t("knowledgeMappings.capability")}
                  disabled={busy || worker.status !== "active"}
                  value={capIndex}
                  onChange={(e) => {
                    setCapIndex(e.target.value);
                    setSourceId("");
                    setPending(null);
                  }}
                >
                  <option value="">{t("knowledgeMappings.select")}</option>
                  {capabilities.map((c, i) => (
                    <option
                      key={`${c.mcp_server}/${c.knowledge_base_ref}`}
                      value={i + 1}
                    >
                      {c.mcp_server} · {c.knowledge_base_ref}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {t("knowledgeMappings.search")}
                <input
                  aria-label={t("knowledgeMappings.search")}
                  value={sourceSearch}
                  disabled={busy || !cap}
                  onChange={(e) => {
                    setSourceSearch(e.target.value);
                    setSourceId("");
                    setPending(null);
                  }}
                />
              </label>
              <label>
                {t("knowledgeMappings.source")}
                <select
                  aria-label={t("knowledgeMappings.source")}
                  disabled={busy || !cap}
                  value={sourceId}
                  onChange={(e) => {
                    setSourceId(e.target.value);
                    setPending(null);
                  }}
                >
                  <option value="">{t("knowledgeMappings.select")}</option>
                  {candidates
                    .filter((s) =>
                      `${s.display_name} ${s.source_id}`
                        .toLowerCase()
                        .includes(sourceSearch.toLowerCase()),
                    )
                    .slice(0, 100)
                    .map((s) => (
                      <option key={s.source_id} value={s.source_id}>
                        {s.display_name} · {s.source_id} · {s.ref}
                      </option>
                    ))}
                </select>
              </label>
              {(worker.status !== "active" ||
                !Number.isInteger(worker.capability_version) ||
                worker.capability_version < 1 ||
                !capabilities.length ||
                (cap && !candidates.length)) && (
                <Typography.Text>
                  {t("knowledgeMappings.missing")}
                </Typography.Text>
              )}
              <Button
                disabled={
                  busy ||
                  !source ||
                  !cap ||
                  worker.status !== "active" ||
                  !Number.isInteger(worker.capability_version) ||
                  worker.capability_version < 1
                }
                onClick={() => {
                  if (source && cap)
                    setPending({
                      create: {
                        ...cap,
                        external_source_id: source.source_id,
                        expected_capability_version: worker.capability_version,
                        expected_connection_revision: source.revision!,
                        expected_resource_revision: source.resource_revision!,
                      },
                    });
                }}
              >
                {t("knowledgeMappings.create")}
              </Button>
              {!items.length && (
                <Typography.Text>
                  {t("knowledgeMappings.empty")}
                </Typography.Text>
              )}
              {items.map((row) => (
                <section key={row.mapping_id} aria-label={row.mapping_id}>
                  <Typography.Text strong>{row.mapping_id}</Typography.Text>
                  <div>
                    {row.external_source_id} ·{" "}
                    {t(`knowledgeMappings.${row.status}`)} · v{row.revision}
                  </div>
                  <div>
                    {t(
                      row.local_snapshot_current === null
                        ? "knowledgeMappings.unchecked"
                        : row.local_snapshot_current
                        ? "knowledgeMappings.current"
                        : "knowledgeMappings.stale",
                    )}
                  </div>
                  <Space>
                    <Button disabled={busy} onClick={() => void inspect(row)}>
                      {t("knowledgeMappings.inspect")}
                    </Button>
                    {row.status === "draft" && (
                      <Button
                        danger
                        disabled={busy}
                        onClick={() => setPending({ revoke: row })}
                      >
                        {t("knowledgeMappings.revoke")}
                      </Button>
                    )}
                  </Space>
                </section>
              ))}
              <Space>
                <Button
                  disabled={busy || offset === 0}
                  onClick={() => void load(Math.max(0, offset - 10))}
                >
                  {t("knowledgeMappings.previous")}
                </Button>
                <span>
                  {items.length
                    ? `${offset + 1}–${offset + items.length}`
                    : "0"}
                </span>
                <Button
                  disabled={busy || items.length < 10}
                  onClick={() => void load(offset + 10)}
                >
                  {t("knowledgeMappings.next")}
                </Button>
              </Space>
            </>
          )}
        </Space>
      </Modal>
      <Modal
        open={open && pending !== null}
        title={t("knowledgeMappings.confirmTitle")}
        onCancel={() => {
          if (!busy) setPending(null);
        }}
        closable={!busy}
        maskClosable={!busy}
        footer={
          <Space>
            <Button disabled={busy} onClick={() => setPending(null)}>
              {t("knowledgeMappings.cancel")}
            </Button>
            <Button disabled={busy} onClick={() => void confirm()}>
              {t("knowledgeMappings.confirm")}
            </Button>
          </Space>
        }
      >
        <p>{t("knowledgeMappings.confirmBoundary")}</p>
        {pending && (
          <p>
            {"create" in pending
              ? `${pending.create.mcp_server} · ${pending.create.knowledge_base_ref} → ${pending.create.external_source_id}`
              : pending.revoke.mapping_id}
          </p>
        )}
      </Modal>
    </>
  );
}
