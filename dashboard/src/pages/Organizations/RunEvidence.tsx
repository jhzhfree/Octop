import SensitiveAccessBoundary from "../../components/SensitiveAccessBoundary";
import { useEffect, useRef, useState } from "react";
import { Alert, Button, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";
import { organizationsApi } from "../../api/modules/organizations";
import type { AcceptanceTarget } from "../../api/modules/deliveryAcceptance";

interface Props {
  target: AcceptanceTarget;
  currentUserId: number;
  canRead: boolean;
  authorizationKey?: string;
}
/** Destroy content, busy state and delayed responses on every authorization change. */
export default function RunEvidence(props: Props) {
  return (
    <SensitiveAccessBoundary>
      <EvidenceSession key={JSON.stringify(props)} {...props} />
    </SensitiveAccessBoundary>
  );
}
function EvidenceSession({ target, canRead, currentUserId }: Props) {
  const { t } = useTranslation();
  const generation = useRef(0);
  const busy = useRef(false);
  const [loading, setLoading] = useState(false);
  const [answer, setAnswer] = useState<string | null>();
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
    if (!canRead || busy.current) return;
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
        !(
          (result.available === false && result.answer === null) ||
          (result.available === true &&
            typeof result.answer === "string" &&
            !!result.answer.trim())
        )
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
  if (!canRead || !Number.isSafeInteger(currentUserId)) return null;
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
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
          {answer ?? t("organizations.evidenceUnavailable")}
        </Typography.Paragraph>
      )}
    </Space>
  );
}
