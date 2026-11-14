import { useEffect, useState } from "react";
import { Alert, Descriptions, Modal, Spin } from "antd";
import { useTranslation } from "react-i18next";
import { runQueueApi, type RunQueueDetail } from "../../api/modules/runQueue";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";

export default function RunDetails({
  organizationId,
  runId,
  onClose,
}: {
  organizationId: string;
  runId: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const timezone = useServerTimezone();
  const [run, setRun] = useState<RunQueueDetail>();
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    void runQueueApi
      .detail(organizationId, runId)
      .then((result) => {
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
  }, [organizationId, runId]);
  return (
    <Modal open title={t("runDetails.title")} onCancel={onClose} footer={null}>
      <Alert type="info" message={t("runDetails.notice")} />
      {error ? (
        <Alert type="error" message={t("runDetails.error")} />
      ) : !run ? (
        <Spin />
      ) : (
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
      )}
    </Modal>
  );
}
