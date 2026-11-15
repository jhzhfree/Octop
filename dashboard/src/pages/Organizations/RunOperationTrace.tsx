import { useEffect, useRef, useState } from "react";
import { Alert, Button, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";
import {
  operationTraceApi,
  validOperationTrace,
  type OperationTrace,
} from "../../api/modules/operationTrace";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";

interface Props {
  organizationId: string;
  runId: string;
  currentUserId: number;
  canRead?: boolean;
}
export default function RunOperationTrace(props: Props) {
  return <TraceSession key={JSON.stringify(props)} {...props} />;
}
function TraceSession({
  organizationId,
  runId,
  currentUserId,
  canRead = true,
}: Props) {
  const { t } = useTranslation();
  const timezone = useServerTimezone();
  const generation = useRef(0);
  const busy = useRef(false);
  const [data, setData] = useState<OperationTrace>();
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(
    () => () => {
      generation.current++;
    },
    [],
  );
  const close = () => {
    generation.current++;
    busy.current = false;
    setData(undefined);
    setLoading(false);
    setFailed(false);
  };
  const read = async () => {
    if (!canRead || busy.current) return;
    busy.current = true;
    const seq = ++generation.current;
    setData(undefined);
    setFailed(false);
    setLoading(true);
    try {
      const result = await operationTraceApi.read(organizationId, runId);
      if (!validOperationTrace(result, organizationId, runId))
        throw new Error("Invalid trace");
      if (generation.current === seq) setData(result);
    } catch {
      if (generation.current === seq) setFailed(true);
    } finally {
      if (generation.current === seq) {
        busy.current = false;
        setLoading(false);
      }
    }
  };
  if (!canRead || !Number.isSafeInteger(currentUserId)) return null;
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Button loading={loading} onClick={() => void read()}>
        {t("operationTrace.view")}
      </Button>
      {(loading || data || failed) && (
        <Button onClick={close}>{t("operationTrace.close")}</Button>
      )}
      {failed && <Alert type="error" message={t("operationTrace.error")} />}
      {data && (
        <section aria-label={t("operationTrace.title")}>
          <Alert type="info" message={t("operationTrace.notice")} />
          <Typography.Paragraph>
            {t("operationTrace.configuration")}:{" "}
            {data.execution_context.agent_id} ·{" "}
            {t(
              `operationTrace.executors.${data.execution_context.configured_executor}`,
            )}{" "}
            ·{" "}
            {data.execution_context.schedule_kind === "one_shot"
              ? t("operationTrace.oneShot")
              : t("operationTrace.unknownSchedule")}{" "}
            · {t("operationTrace.onBehalf")} #
            {data.execution_context.on_behalf_of_user_id}
          </Typography.Paragraph>
          {data.truncated && <p>{t("operationTrace.truncated")}</p>}
          {!data.events.length && <p>{t("operationTrace.empty")}</p>}
          <ol>
            {data.events.map((event) => (
              <li key={event.record_id}>
                {formatServerDateTime(event.recorded_at, timezone)} ({timezone})
                ·{" "}
                {t(
                  `operationTrace.actions.${event.action.replace(/\./g, "_")}`,
                )}{" "}
                ·{" "}
                {event.actor_kind === "user_account"
                  ? `${t("operationTrace.account")} #${event.actor_user_id}`
                  : event.actor_kind === "system"
                  ? t("operationTrace.system")
                  : t("operationTrace.unknownActor")}{" "}
                ·{" "}
                {event.interaction_mode === "not_recorded"
                  ? t("operationTrace.notRecorded")
                  : t("operationTrace.serviceExecution")}{" "}
                · #{event.record_id}
              </li>
            ))}
          </ol>
        </section>
      )}
    </Space>
  );
}
