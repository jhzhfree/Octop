import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Form, Select } from "antd";

import { connectorsApi } from "../../../api/modules/connectors";
import { externalKnowledgeApi } from "../../../api/modules/externalKnowledge";
import { knowledgeBasesApi } from "../../../api/modules/knowledgeBases";
import { useCurrentUser } from "../../../hooks/useCurrentUser";

interface ExpertComposerDefaultsFieldsProps {
  /** Bridge shadow id so knowledge / connector pickers tunnel to the peer. */
  agentId?: string | null;
}

export default function ExpertComposerDefaultsFields({
  agentId,
}: ExpertComposerDefaultsFieldsProps) {
  const { t } = useTranslation();
  const currentUserId = useCurrentUser()?.id ?? null;
  const [knowledgeBases, setKnowledgeBases] = useState<
    { id: string; name: string }[]
  >([]);
  const [knowledgeLoading, setKnowledgeLoading] = useState(false);
  const [connectors, setConnectors] = useState<
    { value: string; label: string }[]
  >([]);
  const [connectorsLoading, setConnectorsLoading] = useState(false);
  const [externalSources, setExternalSources] = useState<
    { value: string; label: string }[]
  >([]);
  const [externalLoading, setExternalLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setKnowledgeLoading(true);
    knowledgeBasesApi
      .list(agentId)
      .then((bases) => {
        if (!cancelled) {
          setKnowledgeBases(
            bases.map((base) => ({ id: base.id, name: base.name })),
          );
        }
      })
      .catch(() => {
        if (!cancelled) setKnowledgeBases([]);
      })
      .finally(() => {
        if (!cancelled) setKnowledgeLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  useEffect(() => {
    let cancelled = false;
    setConnectorsLoading(true);
    connectorsApi
      .listInstances(agentId)
      .then((instances) => {
        if (cancelled) return;
        setConnectors(
          (instances ?? [])
            .filter((item) => item.status === "active" && item.has_credentials)
            .map((item) => ({
              value: item.mcp_server_name,
              label:
                currentUserId !== null && item.owner_user_id !== currentUserId
                  ? `${item.display_name} · ${
                      item.owner_display_name ||
                      item.owner_username ||
                      item.owner_user_id
                    }`
                  : item.display_name || item.mcp_server_name,
            })),
        );
      })
      .catch(() => {
        if (!cancelled) setConnectors([]);
      })
      .finally(() => {
        if (!cancelled) setConnectorsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentId, currentUserId]);

  // External sources are mounted through the binding table, which needs the
  // agent id, so the picker only exists once the expert is saved.
  useEffect(() => {
    if (!agentId) return;
    let cancelled = false;
    setExternalLoading(true);
    externalKnowledgeApi
      .list()
      .then((rows) => {
        if (cancelled) return;
        setExternalSources(
          (rows ?? []).map((row) => ({
            value: row.source_id,
            label: row.display_name || row.source_id,
          })),
        );
      })
      .catch(() => {
        if (!cancelled) setExternalSources([]);
      })
      .finally(() => {
        if (!cancelled) setExternalLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  return (
    <>
      <Form.Item
        name="knowledge_base_ids"
        label={t("experts.knowledgeBasesLabel")}
        extra={t("experts.knowledgeBasesHint")}
      >
        <Select
          mode="multiple"
          allowClear
          showSearch
          optionFilterProp="label"
          loading={knowledgeLoading}
          options={knowledgeBases.map((base) => ({
            value: base.id,
            label: base.name,
          }))}
          placeholder={t("experts.knowledgeBasesPlaceholder")}
        />
      </Form.Item>
      <Form.Item
        name="mcp_servers"
        label={t("experts.connectorsLabel")}
        extra={t("experts.connectorsHint")}
      >
        <Select
          mode="multiple"
          allowClear
          showSearch
          optionFilterProp="label"
          loading={connectorsLoading}
          options={connectors}
          placeholder={t("experts.connectorsPlaceholder")}
        />
      </Form.Item>
      {agentId ? (
        <Form.Item
          name="external_knowledge_source_ids"
          label={t("experts.externalKnowledgeLabel")}
          extra={t("experts.externalKnowledgeHint")}
        >
          <Select
            mode="multiple"
            allowClear
            showSearch
            optionFilterProp="label"
            loading={externalLoading}
            options={externalSources}
            placeholder={t("experts.externalKnowledgePlaceholder")}
          />
        </Form.Item>
      ) : null}
    </>
  );
}
