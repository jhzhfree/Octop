import { useEffect, useRef, useState } from "react";
import { Alert, Button, Space, Typography } from "antd";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import { useTranslation } from "react-i18next";
import {
  queueCategories,
  runQueueApi,
  validRunQueue,
  type QueueCategory,
  type RunQueue as Queue,
} from "../../api/modules/runQueue";

export default function RunQueue(props: {
  organizationId: string;
  canManage: boolean;
}) {
  return (
    <QueueSession
      key={`${props.organizationId}:${props.canManage}`}
      {...props}
    />
  );
}
function QueueSession({
  organizationId,
  canManage,
}: {
  organizationId: string;
  canManage: boolean;
}) {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const [category, setCategory] = useState<QueueCategory>("unreviewed_success");
  const [data, setData] = useState<Queue>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const sequence = useRef(0);
  const busy = useRef(false);
  useEffect(
    () => () => {
      sequence.current++;
    },
    [],
  );
  const load = async (before?: number) => {
    if (!canManage || busy.current) return;
    busy.current = true;
    const seq = ++sequence.current;
    setLoading(true);
    setError(false);
    setData(undefined);
    try {
      const result = await runQueueApi.read(organizationId, category, before);
      if (!validRunQueue(result, organizationId, category, before))
        throw new Error("Invalid queue");
      if (seq === sequence.current) setData(result);
    } catch {
      if (seq === sequence.current) setError(true);
    } finally {
      if (seq === sequence.current) {
        busy.current = false;
        setLoading(false);
      }
    }
  };
  return (
    <Space direction="vertical">
      <Typography.Title level={5}>{t("runQueue.title")}</Typography.Title>
      <Alert type="info" message={t("runQueue.notice")} />
      {!canManage ? (
        <Alert type="warning" message={t("runQueue.forbidden")} />
      ) : (
        <>
          <select
            aria-label={t("runQueue.category")}
            value={category}
            onChange={(event) => {
              sequence.current++;
              busy.current = false;
              setLoading(false);
              setData(undefined);
              setError(false);
              setCategory(event.target.value as QueueCategory);
            }}
          >
            {queueCategories.map((value) => (
              <option key={value} value={value}>
                {t(`runQueue.categories.${value}`)}
              </option>
            ))}
          </select>
          <Button loading={loading} onClick={() => void load()}>
            {t("runQueue.refresh")}
          </Button>
          {error && <Alert type="error" message={t("runQueue.error")} />}
          {data && (
            <>
              <Typography.Text>
                {t(`runQueue.actions.${category}`)}
              </Typography.Text>
              <Typography.Text>
                {t("runQueue.asOf")}:{" "}
                {formatServerDateTime(data.as_of, timeZone)} ({timeZone})
              </Typography.Text>
              {data.items.length === 0 ? (
                <Typography.Text>{t("runQueue.empty")}</Typography.Text>
              ) : (
                <ul>
                  {data.items.map((item) => (
                    <li key={item.run_id}>
                      <code>{item.run_id}</code> ·{" "}
                      {t(`runQueue.categories.${category}`)} ·{" "}
                      {formatServerDateTime(item.created_at, timeZone)}
                    </li>
                  ))}
                </ul>
              )}
              {data.next_before !== null && (
                <Button onClick={() => void load(data.next_before!)}>
                  {t("runQueue.next")}
                </Button>
              )}
            </>
          )}
        </>
      )}
    </Space>
  );
}
