import RunDetails from "./RunDetails";
import { useCurrentUser } from "../../hooks/useCurrentUser";
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

type WorkerOption = {
  worker_id: string;
  organization_id: string;
  name: string;
};

export default function RunQueue(props: {
  organizationId: string;
  canManage: boolean;
  workers?: WorkerOption[];
  goalId?: string;
}) {
  const user = useCurrentUser();
  const deliveryUserId =
    user &&
    Number.isSafeInteger(user.id) &&
    (user.role === "admin" || user.permissions?.includes("tasks"))
      ? user.id
      : undefined;
  return (
    <QueueSession
      key={JSON.stringify([
        props.organizationId,
        props.canManage,
        props.goalId,
        user?.id,
        user?.role,
        user?.permissions,
        props.workers?.map((worker) => [
          worker.worker_id,
          worker.organization_id,
        ]),
      ])}
      deliveryUserId={deliveryUserId}
      {...props}
    />
  );
}
function QueueSession({
  organizationId,
  canManage,
  deliveryUserId,
  workers = [],
  goalId,
}: {
  organizationId: string;
  canManage: boolean;
  deliveryUserId?: number;
  workers?: WorkerOption[];
  goalId?: string;
}) {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const [workerId, setWorkerId] = useState<string>();
  const [detailRun, setDetailRun] = useState<string>();
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
    setDetailRun(undefined);
    try {
      const result = goalId
        ? await runQueueApi.read(
            organizationId,
            category,
            before,
            workerId,
            goalId,
          )
        : workerId
        ? await runQueueApi.read(organizationId, category, before, workerId)
        : await runQueueApi.read(organizationId, category, before);
      if (
        !validRunQueue(
          result,
          organizationId,
          category,
          before,
          workerId,
          goalId,
        )
      )
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
      {canManage && detailRun && (
        <RunDetails
          key={`${organizationId}:${detailRun}`}
          organizationId={organizationId}
          runId={detailRun}
          currentUserId={deliveryUserId}
          onClose={() => setDetailRun(undefined)}
          onChanged={() => setData(undefined)}
          onOpenRun={(runId) => {
            setData(undefined);
            setDetailRun(runId);
          }}
        />
      )}
      <Typography.Title level={5}>{t("runQueue.title")}</Typography.Title>
      <Alert type="info" message={t("runQueue.notice")} />
      {!canManage ? (
        <Alert type="warning" message={t("runQueue.forbidden")} />
      ) : (
        <>
          {workers.length > 0 && (
            <>
              <select
                aria-label={t("runQueue.worker")}
                value={workerId ?? ""}
                onChange={(event) => {
                  sequence.current++;
                  busy.current = false;
                  setLoading(false);
                  setData(undefined);
                  setDetailRun(undefined);
                  setError(false);
                  setWorkerId(event.target.value || undefined);
                }}
              >
                <option value="">{t("runQueue.allWorkers")}</option>
                {workers
                  .filter((worker) => worker.organization_id === organizationId)
                  .map((worker) => (
                    <option key={worker.worker_id} value={worker.worker_id}>
                      {worker.name} ({worker.worker_id})
                    </option>
                  ))}
              </select>
              <Typography.Text>{t("runQueue.workerScope")}</Typography.Text>
            </>
          )}
          <select
            aria-label={t("runQueue.category")}
            value={category}
            onChange={(event) => {
              setDetailRun(undefined);
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
                      <Button onClick={() => setDetailRun(item.run_id)}>
                        {item.run_id}
                      </Button>{" "}
                      · {t(`runQueue.categories.${category}`)} ·{" "}
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
