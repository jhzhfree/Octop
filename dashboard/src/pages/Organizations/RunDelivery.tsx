import { Alert, Space } from "antd";
import { useTranslation } from "react-i18next";
import RunEvidence from "./RunEvidence";
import type { AcceptanceTarget } from "../../api/modules/deliveryAcceptance";
import DeliveryAcceptance from "./DeliveryAcceptance";
import MattermostPublication from "./MattermostPublication";

/** Mounted only inside the current authorized, successful queue detail session. */
export default function RunDelivery({
  target,
  currentUserId,
}: {
  target: AcceptanceTarget;
  currentUserId: number;
}) {
  const { t } = useTranslation();
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Alert type="info" message={t("runDetails.deliveryNotice")} />
      <RunEvidence target={target} currentUserId={currentUserId} canRead />
      <DeliveryAcceptance
        target={target}
        currentUserId={currentUserId}
        canReview
      />
      <MattermostPublication
        target={target}
        currentUserId={currentUserId}
        canRead
        canCreate={false}
        canApprove={false}
        canRevoke={false}
      />
    </Space>
  );
}
