import { useEffect, useRef, useState } from "react";
import { Alert, Button, Input, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";
import type { AcceptanceTarget } from "../../api/modules/deliveryAcceptance";
import {
  reportAttachmentApi,
  validReportAttachment,
  validReportPreview,
  type ReportAttachment as Attachment,
  type ReportPreview,
} from "../../api/modules/reportAttachment";
import { reportAttachmentMessages } from "./reportAttachmentMessages";

interface Props {
  target: AcceptanceTarget;
  currentUserId: number;
  canAttach: boolean;
}
export default function ReportAttachment(props: Props) {
  return (
    <AttachmentSession
      key={JSON.stringify([props.target, props.currentUserId, props.canAttach])}
      {...props}
    />
  );
}
function AttachmentSession({ target, canAttach }: Props) {
  const { i18n } = useTranslation();
  const m =
    reportAttachmentMessages[
      (i18n.resolvedLanguage || i18n.language || "en").startsWith("zh")
        ? "zh"
        : "en"
    ];
  const generation = useRef(0);
  const busy = useRef(false);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [reportId, setReportId] = useState("");
  const [preview, setPreview] = useState<ReportPreview>();
  const [attached, setAttached] = useState<Attachment>();
  useEffect(
    () => () => {
      generation.current += 1;
    },
    [],
  );
  const close = () => {
    generation.current += 1;
    busy.current = false;
    setOpen(false);
    setLoading(false);
    setFailed(false);
    setReportId("");
    setPreview(undefined);
    setAttached(undefined);
  };
  const perform = async (operation: () => Promise<void>) => {
    if (!canAttach || busy.current) return;
    busy.current = true;
    setLoading(true);
    setFailed(false);
    const seq = ++generation.current;
    try {
      await operation();
    } catch {
      if (generation.current === seq) {
        setFailed(true);
        setPreview(undefined);
      }
    } finally {
      if (generation.current === seq) {
        busy.current = false;
        setLoading(false);
      }
    }
  };
  const read = () =>
    perform(async () => {
      setOpen(true);
      const seq = generation.current;
      const result = await reportAttachmentApi.get(target);
      if (result !== null && !validReportAttachment(result, target))
        throw new Error("invalid attachment");
      if (generation.current === seq) setAttached(result ?? undefined);
    });
  const loadPreview = () =>
    perform(async () => {
      const seq = generation.current;
      const id = reportId.trim();
      const result = await reportAttachmentApi.preview(target, id);
      if (!validReportPreview(result, id)) throw new Error("invalid preview");
      if (generation.current === seq) setPreview(result);
    });
  const attach = () => {
    if (!preview) return;
    const expected = preview;
    return perform(async () => {
      const seq = generation.current;
      const result = await reportAttachmentApi.attach(target, expected);
      if (
        !validReportAttachment(result, target) ||
        result.report_run_id !== expected.report_run_id ||
        result.report_version !== expected.report_version ||
        result.report_content_hash !== expected.report_content_hash ||
        result.content !== expected.content
      )
        throw new Error("invalid attachment");
      if (generation.current === seq) {
        setAttached(result);
        setPreview(undefined);
      }
    });
  };
  if (!canAttach) return null;
  const content = attached ?? preview;
  return (
    <section aria-label={m.open}>
      {!open ? (
        <Button onClick={() => void read()}>{m.open}</Button>
      ) : (
        <Space direction="vertical" style={{ width: "100%" }}>
          <Alert type="info" message={m.boundary} />
          <Button onClick={close}>{m.close}</Button>
          {loading && (
            <Typography.Text role="status">{m.loading}</Typography.Text>
          )}
          {failed ? (
            <Alert type="error" message={m.failed} />
          ) : (
            <>
              {attached ? (
                <Alert type="success" message={m.attached} />
              ) : (
                !preview && (
                  <>
                    <Typography.Text>{m.hint}</Typography.Text>
                    <Input
                      aria-label={m.reportId}
                      value={reportId}
                      disabled={loading}
                      onChange={(event) => setReportId(event.target.value)}
                    />
                    <Button
                      disabled={loading || !reportId.trim()}
                      onClick={() => void loadPreview()}
                    >
                      {m.preview}
                    </Button>
                  </>
                )
              )}
              {content && (
                <>
                  <Typography.Text>
                    {content.report_run_id} · {m.version}{" "}
                    {content.report_version}
                  </Typography.Text>
                  <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>
                    {content.content}
                  </Typography.Paragraph>
                  <Typography.Text>
                    {m.hash}: {content.report_content_hash}
                  </Typography.Text>
                </>
              )}
              {preview && !attached && (
                <>
                  <Alert type="warning" message={m.sharing} />
                  <Button disabled={loading} onClick={() => void attach()}>
                    {m.confirm}
                  </Button>
                  <Button
                    disabled={loading}
                    onClick={() => setPreview(undefined)}
                  >
                    {m.cancel}
                  </Button>
                </>
              )}
            </>
          )}
        </Space>
      )}
    </section>
  );
}
