import { useState } from "react";
import { Alert, Modal, Select, Typography } from "antd";
import { useTranslation } from "react-i18next";
import type {
  DigitalWorker,
  EnterpriseScene,
} from "../../api/modules/organizations";

export default function ScenePlanGuide({
  scene,
  workers,
  canManage,
  onSelect,
  onClose,
}: {
  scene: EnterpriseScene;
  workers: DigitalWorker[];
  canManage: boolean;
  onSelect: (worker: DigitalWorker) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [workerId, setWorkerId] = useState<string>();
  const matches = workers.filter(
    (worker) =>
      worker.organization_id === scene.organization_id &&
      worker.position_id === scene.position_id &&
      worker.agent_id === scene.agent_id &&
      worker.status === "active",
  );
  const selected = matches.find((worker) => worker.worker_id === workerId);
  return (
    <Modal
      open
      title={t("organizations.scenePlanGuideTitle")}
      onCancel={onClose}
      okText={t("organizations.scenePlanGuideContinue")}
      okButtonProps={{ disabled: !selected || !scene.enabled }}
      onOk={() => {
        if (selected && scene.enabled) onSelect(selected);
      }}
    >
      <Typography.Paragraph>{scene.name}</Typography.Paragraph>
      <Alert type="info" message={t("organizations.scenePlanGuideSteps")} />
      {matches.length === 0 ? (
        <Alert
          type="warning"
          message={t(
            canManage
              ? "organizations.scenePlanGuideNoWorker"
              : "organizations.scenePlanGuideAskManager",
          )}
        />
      ) : (
        <Select
          style={{ width: "100%" }}
          aria-label={t("organizations.scenePlanGuideWorker")}
          placeholder={t("organizations.scenePlanGuideWorker")}
          value={workerId}
          onChange={setWorkerId}
          options={matches.map((worker) => ({
            value: worker.worker_id,
            label: worker.name,
          }))}
        />
      )}
      <Typography.Paragraph type="secondary">
        {t(
          canManage
            ? "organizations.scenePlanGuideNoPlan"
            : "organizations.scenePlanGuideReadOnly",
        )}
      </Typography.Paragraph>
    </Modal>
  );
}
