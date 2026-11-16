import InspectionRevision from "./InspectionRevision";
import AcceptanceFeedback from "./AcceptanceFeedback";
import { Alert, Space } from "antd";
import { useTranslation } from "react-i18next";
import RunEvidence from "./RunEvidence";
import type {
  AcceptanceTarget,
  AcceptanceDecision,
  AcceptanceRecord,
} from "../../api/modules/deliveryAcceptance";
import DeliveryAcceptance from "./DeliveryAcceptance";
import MattermostPublication from "./MattermostPublication";

/** Mounted only inside the current authorized, successful queue detail session. */
export default function RunDelivery({
  target,
  currentUserId,
  decision = null,
  onDecided,
  onOpenRun,
}: {
  target: AcceptanceTarget;
  currentUserId: number;
  decision?: AcceptanceDecision | null;
  onDecided?: (record: AcceptanceRecord) => void;
  onOpenRun?: (runId: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Alert type="info" message={t("runDetails.deliveryNotice")} />
      {decision === "changes_requested" && (
        <InspectionRevision
          target={target}
          currentUserId={currentUserId}
          onOpenRun={onOpenRun}
        />
      )}
      <AcceptanceFeedback
        key={decision ?? "unreviewed"}
        target={target}
        currentUserId={currentUserId}
      />
      <RunEvidence target={target} currentUserId={currentUserId} canRead />
      <DeliveryAcceptance
        onDecided={onDecided}
        target={target}
        currentUserId={currentUserId}
        canReview
      />
      <MattermostPublication
        key={decision ?? "unreviewed"}
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
