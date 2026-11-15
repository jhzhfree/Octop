import { useEffect, useRef, useState } from "react";
import { Alert, Button, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";
import { organizationsApi } from "../../api/modules/organizations";
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
  const generation = useRef(0);
  const busy = useRef(false);
  const [loading, setLoading] = useState(false);
  const [answer, setAnswer] = useState<string>();
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
    setLoading(false);
    setAnswer(undefined);
    setFailed(false);
  };
  const read = async () => {
    if (busy.current) return;
    busy.current = true;
    const seq = ++generation.current;
    setLoading(true);
    setAnswer(undefined);
    setFailed(false);
    try {
      const result = await organizationsApi.sceneRunEvidence(
        target.organizationId,
        target.runId,
      );
      if (
        result.run_id !== target.runId ||
        result.available !== true ||
        typeof result.answer !== "string" ||
        !result.answer.trim()
      )
        throw new Error("Unavailable evidence");
      if (generation.current === seq) setAnswer(result.answer);
    } catch {
      if (generation.current === seq) setFailed(true);
    } finally {
      if (generation.current === seq) {
        busy.current = false;
        setLoading(false);
      }
    }
  };
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Alert type="info" message={t("runDetails.deliveryNotice")} />
      <Button loading={loading} onClick={() => void read()}>
        {t("organizations.viewEvidence")}
      </Button>
      {(loading || answer !== undefined || failed) && (
        <Button onClick={close}>{t("runDetails.closeEvidence")}</Button>
      )}
      {failed && (
        <Alert type="error" message={t("organizations.evidenceLoadFailed")} />
      )}
      {answer !== undefined && (
        <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>
          {answer}
        </Typography.Paragraph>
      )}
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
