import SensitiveAccessBoundary from "../../components/SensitiveAccessBoundary";
import { useEffect, useRef, useState } from "react";
import { Alert, Button, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";
import type { AcceptanceTarget } from "../../api/modules/deliveryAcceptance";
import {
  mattermostPublicationApi,
  validPublication,
  validPublicationCandidate,
  type MattermostPublication as Publication,
} from "../../api/modules/mattermostPublication";
import { mattermostPublicationMessages } from "./mattermostPublicationMessages";

interface Props {
  target: AcceptanceTarget;
  currentUserId: number;
  canRead: boolean;
  canCreate: boolean;
  canApprove: boolean;
  canRevoke: boolean;
}
type Decision = "approve" | "revoke";
/** Identity, organization, run and role changes destroy the old content and confirmation. */
export default function MattermostPublication(props: Props) {
  return (
    <SensitiveAccessBoundary>
      <PublicationSession key={JSON.stringify(props)} {...props} />
    </SensitiveAccessBoundary>
  );
}
function PublicationSession({
  target,
  currentUserId,
  canRead,
  canCreate,
  canApprove,
  canRevoke,
}: Props) {
  const { i18n } = useTranslation();
  const m =
    mattermostPublicationMessages[
      (i18n.resolvedLanguage || i18n.language || "en").startsWith("zh")
        ? "zh"
        : "en"
    ];
  const generation = useRef(0);
  const busy = useRef(false);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [mustRead, setMustRead] = useState(false);
  const [record, setRecord] = useState<Publication>();
  const [pending, setPending] = useState<Decision>();
  useEffect(
    () => () => {
      generation.current++;
    },
    [],
  );
  const close = () => {
    generation.current++;
    busy.current = false;
    setOpen(false);
    setLoading(false);
    setFailed(false);
    setRecord(undefined);
    setPending(undefined);
  };
  const perform = async (
    operation: (active: () => boolean) => Promise<Publication>,
    decision?: Decision | "create",
  ) => {
    if (!canRead || busy.current) return;
    const previous = record;
    busy.current = true;
    const seq = ++generation.current;
    setOpen(true);
    setLoading(true);
    setFailed(false);
    setPending(undefined);
    if (decision && decision !== "create") setMustRead(true);
    // Never keep displaying stale content during a read or uncertain mutation.
    setRecord(undefined);
    try {
      const result = await operation(() => generation.current === seq);
      if (
        !validPublication(result, target) ||
        (decision &&
          decision !== "create" &&
          (!previous ||
            result.publication_sha256 !== previous.publication_sha256 ||
            result.proposed_by !== previous.proposed_by ||
            result.worker_id !== previous.worker_id ||
            result.worker_version !== previous.worker_version ||
            result.intent_revision !== previous.intent_revision ||
            result.configuration_revision !== previous.configuration_revision ||
            result.report_sha256 !== previous.report_sha256 ||
            result.instance_key !== previous.instance_key ||
            result.team_id !== previous.team_id ||
            result.channel_id !== previous.channel_id ||
            result.bot_id !== previous.bot_id ||
            result.status !==
              (decision === "approve" ? "blocked_not_connected" : "revoked") ||
            (decision === "approve" &&
              (result.content !== previous.content ||
                result.approved_by !== currentUserId))))
      )
        throw new Error("invalid publication");
      if (generation.current === seq) {
        setRecord(result);
        setMustRead(false);
      }
    } catch {
      if (generation.current === seq) setFailed(true);
    } finally {
      if (generation.current === seq) {
        busy.current = false;
        setLoading(false);
      }
    }
  };
  const approveAllowed =
    canApprove &&
    record?.status === "pending_approval" &&
    record.proposed_by !== currentUserId;
  const revokeAllowed =
    canRevoke &&
    record &&
    ["pending_approval", "blocked_not_connected"].includes(record.status);
  const decide = () => {
    if (
      !record ||
      !pending ||
      (pending === "approve" ? !approveAllowed : !revokeAllowed)
    )
      return;
    const digest = record.publication_sha256;
    const decision = pending;
    void perform(
      () => mattermostPublicationApi.decide(target, decision, digest),
      decision,
    );
  };
  const create = () => {
    if (!canCreate || mustRead || record) return;
    void perform(async (active) => {
      const candidate = await mattermostPublicationApi.candidate(target);
      if (!active() || !validPublicationCandidate(candidate))
        throw new Error("invalid candidate");
      setMustRead(true);
      const result = await mattermostPublicationApi.create(target, candidate);
      if (
        result.proposed_by !== currentUserId ||
        result.report_sha256 !== candidate.expected_report_sha256 ||
        result.worker_version !== candidate.expected_worker_version ||
        result.intent_revision !== candidate.expected_intent_revision ||
        result.configuration_revision !==
          candidate.expected_configuration_revision
      )
        throw new Error("invalid creation");
      return result;
    }, "create");
  };
  if (!canRead) return null;
  return (
    <section aria-label={m.open}>
      {!open ? (
        <Button
          onClick={() =>
            void perform(() => mattermostPublicationApi.get(target))
          }
        >
          {m.open}
        </Button>
      ) : (
        <Space direction="vertical" style={{ width: "100%" }}>
          <Typography.Title level={5}>{m.open}</Typography.Title>
          <Alert type="warning" message={m.boundary} />
          <Typography.Paragraph>{m.prerequisite}</Typography.Paragraph>
          <Space>
            <Button onClick={close}>{m.close}</Button>
            <Button
              disabled={loading}
              onClick={() =>
                void perform(() => mattermostPublicationApi.get(target))
              }
            >
              {m.refresh}
            </Button>
          </Space>
          {canCreate && !record && (
            <Button disabled={loading || mustRead} onClick={create}>
              {m.create}
            </Button>
          )}
          {loading && (
            <Typography.Text role="status">{m.loading}</Typography.Text>
          )}
          {failed && <Alert type="error" message={m.failed} />}
          {record && (
            <>
              <div role="status">{m[record.status]}</div>
              <Typography.Text>
                {m.proposer}: {record.proposed_by}
              </Typography.Text>
              {record.approved_by !== null && (
                <Typography.Text>
                  {m.approver}: {record.approved_by}
                </Typography.Text>
              )}
              <div role="group" aria-label={m.destination}>
                <strong>{m.destination}</strong>
                <p>
                  {m.instance}: {record.instance_key}
                </p>
                <p>
                  {m.team}: {record.team_id}
                </p>
                <p>
                  {m.channel}: {record.channel_id}
                </p>
                <p>
                  {m.bot}: {record.bot_id}
                </p>
              </div>
              {record.status !== "revoked" && (
                <div role="group" aria-label={m.content}>
                  <strong>{m.content}</strong>
                  <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>
                    {record.content}
                  </Typography.Paragraph>
                </div>
              )}
              <Typography.Text>
                {m.reportHash}: {record.report_sha256}
              </Typography.Text>
              <Typography.Text>
                {m.hash}: {record.publication_sha256}
              </Typography.Text>
              <Typography.Paragraph>{m.independent}</Typography.Paragraph>
              <Space>
                {approveAllowed && (
                  <Button
                    disabled={loading || !!pending}
                    onClick={() => setPending("approve")}
                  >
                    {m.approve}
                  </Button>
                )}
                {revokeAllowed && (
                  <Button
                    disabled={loading || !!pending}
                    onClick={() => setPending("revoke")}
                  >
                    {m.revoke}
                  </Button>
                )}
              </Space>
              {pending && (
                <div role="group" aria-label={m.confirm}>
                  <p>
                    {pending === "approve" ? m.approveConfirm : m.revokeConfirm}
                  </p>
                  <Button disabled={loading} onClick={decide}>
                    {m.confirm}
                  </Button>
                  <Button
                    disabled={loading}
                    onClick={() => setPending(undefined)}
                  >
                    {m.cancel}
                  </Button>
                </div>
              )}
            </>
          )}
        </Space>
      )}
    </section>
  );
}
