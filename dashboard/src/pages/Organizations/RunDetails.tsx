import InspectionRevision from "./InspectionRevision";
import RunApproval from "./RunApproval";
import { organizationsApi } from "../../api/modules/organizations";
import RunOperationTrace from "./RunOperationTrace";
import RunDelivery from "./RunDelivery";
import { useEffect, useState } from "react";
import { Alert, Button, Descriptions, Modal, Spin } from "antd";
import { useTranslation } from "react-i18next";
import { runQueueApi, type RunQueueDetail } from "../../api/modules/runQueue";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";

interface Props {
  organizationId: string;
  runId: string;
  currentUserId?: number;
  onClose: () => void;
  onChanged?: () => void;
  onOpenRun?: (runId: string) => void;
}

export default function RunDetails(props: Props) {
  return (
    <DetailSession
      key={JSON.stringify([
        props.organizationId,
        props.runId,
        props.currentUserId,
      ])}
      {...props}
    />
  );
}
function DetailSession({
  organizationId,
  runId,
  onClose,
  currentUserId,
  onChanged,
  onOpenRun,
}: Props) {
  const { t } = useTranslation();
  const timezone = useServerTimezone();
  const [run, setRun] = useState<RunQueueDetail>();
  const [error, setError] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const reload = () => {
    setRun(undefined);
    setError(false);
    setRefresh((value) => value + 1);
  };
  useEffect(() => {
    let active = true;
    const read = async () => {
      if (refresh > 0 && currentUserId !== undefined) {
        const current = await organizationsApi.sceneRun(organizationId, runId);
        if (
          current.organization_id !== organizationId ||
          current.run_id !== runId
        )
          throw new Error("Mismatched run");
        if (!active) return;
      }
      return runQueueApi.detail(organizationId, runId);
    };
    void read()
      .then((result) => {
        if (!active || !result) return;
        if (
          result.organization_id !== organizationId ||
          result.run_id !== runId
        )
          throw new Error("Mismatched run");
        if (active) setRun(result);
      })
      .catch(() => {
        if (active) setError(true);
      });
    return () => {
      active = false;
    };
  }, [organizationId, runId, refresh, currentUserId]);
  return (
    <Modal open title={t("runDetails.title")} onCancel={onClose} footer={null}>
      <Alert type="info" message={t("runDetails.notice")} />
      <Button disabled={!run && !error} onClick={reload}>
        {t("runDetails.refresh")}
      </Button>
      {error ? (
        <Alert type="error" message={t("runDetails.error")} />
      ) : !run ? (
        <Spin />
      ) : (
        <>
          <Descriptions column={1}>
            <Descriptions.Item label={t("runDetails.run")}>
              {run.run_id}
            </Descriptions.Item>
            <Descriptions.Item label={t("runDetails.scene")}>
              {run.scene_name} (v{run.scene_version})
            </Descriptions.Item>
            <Descriptions.Item label={t("runDetails.status")}>
              {t(`organizations.runStatuses.${run.status}`)}
            </Descriptions.Item>
            <Descriptions.Item label={t("runDetails.created")}>
              {formatServerDateTime(run.created_at, timezone)} ({timezone})
            </Descriptions.Item>
            <Descriptions.Item label={t("runDetails.plan")}>
              {run.plan_id ?? "—"}
            </Descriptions.Item>
          </Descriptions>
          {run.status === "awaiting_approval" &&
            currentUserId !== undefined && (
              <RunApproval
                organizationId={organizationId}
                runId={runId}
                currentUserId={currentUserId}
                onDecided={() => {
                  onChanged?.();
                  reload();
                }}
              />
            )}
          {currentUserId !== undefined && (
            <RunOperationTrace
              organizationId={organizationId}
              runId={runId}
              currentUserId={currentUserId}
            />
          )}
          {run.status === "failed" && currentUserId !== undefined && (
            <InspectionRevision
              target={{ organizationId, runId }}
              currentUserId={currentUserId}
              fromFailure
              onOpenRun={onOpenRun}
            />
          )}
          {run.status === "succeeded" && currentUserId !== undefined && (
            <RunDelivery
              onOpenRun={onOpenRun}
              decision={run.decision}
              onDecided={(record) => {
                setRun((current) =>
                  current ? { ...current, decision: record.decision } : current,
                );
                onChanged?.();
              }}
              target={{ organizationId, runId }}
              currentUserId={currentUserId}
            />
          )}
        </>
      )}
    </Modal>
  );
}
