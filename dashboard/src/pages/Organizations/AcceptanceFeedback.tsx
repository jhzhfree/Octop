import { useEffect, useRef, useState } from "react";
import { Alert, Button, Space } from "antd";
import { useTranslation } from "react-i18next";
import SensitiveAccessBoundary from "../../components/SensitiveAccessBoundary";
import {
  deliveryAcceptanceApi,
  validAcceptanceRecord,
  type AcceptanceRecord,
  type AcceptanceTarget,
} from "../../api/modules/deliveryAcceptance";
import { deliveryAcceptanceMessages } from "./deliveryAcceptanceMessages";

export default function AcceptanceFeedback(props: {
  target: AcceptanceTarget;
  currentUserId: number;
}) {
  return (
    <SensitiveAccessBoundary>
      <FeedbackSession key={JSON.stringify(props)} {...props} />
    </SensitiveAccessBoundary>
  );
}
function FeedbackSession({ target }: { target: AcceptanceTarget }) {
  const { i18n } = useTranslation();
  const m =
    deliveryAcceptanceMessages[
      (i18n.resolvedLanguage || i18n.language || "en").startsWith("zh")
        ? "zh"
        : "en"
    ];
  const generation = useRef(0);
  const busy = useRef(false);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [record, setRecord] = useState<AcceptanceRecord | null>();
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
    setFailed(false);
    setRecord(undefined);
  };
  const read = async () => {
    if (busy.current) return;
    const seq = ++generation.current;
    busy.current = true;
    setLoading(true);
    setFailed(false);
    setRecord(undefined);
    try {
      const value = await deliveryAcceptanceApi.feedback(target);
      if (value !== null && !validAcceptanceRecord(value, target))
        throw new Error("Invalid feedback");
      if (seq === generation.current) setRecord(value);
    } catch {
      if (seq === generation.current) setFailed(true);
    } finally {
      if (seq === generation.current) {
        busy.current = false;
        setLoading(false);
      }
    }
  };
  return (
    <Space direction="vertical">
      <Button loading={loading} onClick={() => void read()}>
        {m.feedback}
      </Button>
      {(loading || failed || record !== undefined) && (
        <Button onClick={close}>{m.close}</Button>
      )}
      {failed && <Alert type="error" message={m.failed} />}
      {record === null && <p>{m.feedbackEmpty}</p>}
      {record && (
        <section aria-label={m.feedback}>
          <p>
            {m[record.decision]} · {m.reviewer}: #{record.reviewer_id}
          </p>
          <p style={{ whiteSpace: "pre-wrap" }}>
            {record.review_note || m.noteMissing}
          </p>
        </section>
      )}
    </Space>
  );
}
