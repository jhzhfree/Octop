import { useEffect, useRef, useState } from "react";
import { Alert, Button, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";
import {
  deliveryAcceptanceApi,
  validAcceptanceRecord,
  validAcceptanceReview,
  type AcceptanceTarget,
  type AcceptanceReview,
  type AcceptanceDecision,
} from "../../api/modules/deliveryAcceptance";
import { deliveryAcceptanceMessages } from "./deliveryAcceptanceMessages";

interface DeliveryAcceptanceProps {
  target: AcceptanceTarget;
  canReview: boolean;
  currentUserId: number;
}

/** Every authorization context change destroys the previous evidence session. */
export default function DeliveryAcceptance(props: DeliveryAcceptanceProps) {
  return (
    <AcceptanceSession
      key={JSON.stringify([
        props.target.organizationId,
        props.target.runId,
        props.canReview,
        props.currentUserId,
      ])}
      {...props}
    />
  );
}

/** canReview gates the entry only; the server determines actual evidence access. */
function AcceptanceSession({ target, canReview }: DeliveryAcceptanceProps) {
  const { i18n } = useTranslation();
  const m =
    deliveryAcceptanceMessages[
      (i18n.resolvedLanguage || i18n.language || "en").startsWith("zh")
        ? "zh"
        : "en"
    ];
  const key = JSON.stringify([target.organizationId, target.runId, canReview]);
  const generation = useRef(0);
  const busy = useRef(false);
  const [state, setState] = useState<{
    key: string;
    open?: boolean;
    loading?: boolean;
    failed?: boolean;
    review?: AcceptanceReview;
    pending?: AcceptanceDecision;
  }>({ key });
  useEffect(() => {
    generation.current += 1;
    busy.current = false;
    return () => {
      generation.current += 1;
      busy.current = false;
    };
  }, [key]);
  const current = state.key === key ? state : undefined;
  const close = () => {
    generation.current += 1;
    busy.current = false;
    setState({ key });
  };
  const open = async () => {
    if (!canReview || busy.current) return;
    busy.current = true;
    const seq = ++generation.current;
    setState({ key, open: true, loading: true });
    try {
      const review = await deliveryAcceptanceApi.review(target);
      if (!validAcceptanceReview(review, target))
        throw new Error("invalid review");
      if (generation.current === seq) setState({ key, open: true, review });
    } catch {
      if (generation.current === seq)
        setState({ key, open: true, failed: true });
    } finally {
      if (generation.current === seq) busy.current = false;
    }
  };
  const decide = async () => {
    if (!canReview || busy.current || !current?.review || !current.pending)
      return;
    busy.current = true;
    const seq = ++generation.current;
    const review = current.review;
    const decision = current.pending;
    setState({ key, open: true, review, loading: true });
    try {
      const record = await deliveryAcceptanceApi.decide(
        target,
        decision,
        review,
      );
      if (
        !validAcceptanceRecord(record, target) ||
        record.decision !== decision ||
        record.evidence_sha256 !== review.evidence_sha256 ||
        record.snapshot_sha256 !== review.snapshot_sha256
      )
        throw new Error("invalid decision");
      if (generation.current === seq)
        setState({
          key,
          open: true,
          review: { ...review, acceptance: record },
        });
    } catch {
      if (generation.current === seq)
        setState({ key, open: true, failed: true });
    } finally {
      if (generation.current === seq) busy.current = false;
    }
  };
  if (!canReview) return null;
  return (
    <section aria-label={m.title}>
      {!current?.open ? (
        <Button onClick={() => void open()}>{m.open}</Button>
      ) : (
        <Space direction="vertical" style={{ width: "100%" }}>
          <Typography.Title level={5}>{m.title}</Typography.Title>
          <Alert type="warning" showIcon message={m.boundary} />
          <Button onClick={close}>{m.close}</Button>
          {current.loading && (
            <Typography.Text role="status">
              {i18n.language?.startsWith("zh") ? "处理中…" : "Processing…"}
            </Typography.Text>
          )}
          {current.failed && <Alert type="error" message={m.failed} />}
          {current.review && (
            <>
              <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>
                {current.review.answer}
              </Typography.Paragraph>
              <Typography.Text>
                {m.evidence}: {current.review.evidence_sha256}
              </Typography.Text>
              <Typography.Text>
                {m.snapshot}: {current.review.snapshot_sha256}
              </Typography.Text>
              {current.review.acceptance ? (
                <div role="status">
                  <p>{m[current.review.acceptance.decision]}</p>
                  <p>
                    {m.reviewer}: {current.review.acceptance.reviewer_id}
                  </p>
                  <p>
                    {m.time}:{" "}
                    {new Date(
                      current.review.acceptance.reviewed_at * 1000,
                    ).toLocaleString()}
                  </p>
                </div>
              ) : (
                <>
                  <Space>
                    <Button
                      disabled={current.loading}
                      onClick={() =>
                        setState({ ...current, key, pending: "accepted" })
                      }
                    >
                      {m.accept}
                    </Button>
                    <Button
                      disabled={current.loading}
                      onClick={() =>
                        setState({
                          ...current,
                          key,
                          pending: "changes_requested",
                        })
                      }
                    >
                      {m.changes}
                    </Button>
                  </Space>
                  {current.pending && (
                    <div role="group" aria-label={m.confirmation}>
                      <p>
                        {m.confirmation} {m[current.pending]}
                      </p>
                      <Button
                        disabled={current.loading}
                        onClick={() => void decide()}
                      >
                        {m.confirm}
                      </Button>
                      <Button
                        disabled={current.loading}
                        onClick={() =>
                          setState({ ...current, key, pending: undefined })
                        }
                      >
                        {m.cancel}
                      </Button>
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </Space>
      )}
    </section>
  );
}
