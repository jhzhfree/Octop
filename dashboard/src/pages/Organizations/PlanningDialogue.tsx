import { Alert, Button, Input, Modal, Space, Typography } from "antd";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  organizationsApi,
  type DigitalWorkerPlan,
  type PlanningDialogueMessage,
  type PlanningDialogueResponse,
} from "../../api/modules/organizations";
import { apiErrorMessage } from "../../utils/apiError";

interface Props {
  organizationId: string;
  workerId: string;
  workerVersion: number;
  goalId: string;
  goalTitle: string;
  onDraft: (
    draft: Pick<DigitalWorkerPlan, "title" | "approach" | "steps">,
  ) => void;
}

/** A stateless, tool-free model dialogue. Only the existing plan editor saves. */
export default function PlanningDialogue({
  organizationId,
  workerId,
  workerVersion,
  goalId,
  goalTitle,
  onDraft,
}: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<PlanningDialogueMessage[]>([]);
  const [result, setResult] = useState<PlanningDialogueResponse | null>(null);
  const [previousDraft, setPreviousDraft] =
    useState<PlanningDialogueResponse["draft"]>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const activeRequest = useRef<AbortController | null>(null);
  const generation = useRef(0);

  useEffect(
    () => () => {
      generation.current += 1;
      activeRequest.current?.abort();
    },
    [],
  );

  function close() {
    generation.current += 1;
    activeRequest.current?.abort();
    activeRequest.current = null;
    setOpen(false);
    setBusy(false);
    setInput("");
    setMessages([]);
    setResult(null);
    setPreviousDraft(null);
    setError("");
  }

  async function send() {
    const content = input.trim();
    if (
      !content ||
      content.length > 4000 ||
      activeRequest.current ||
      messages.length >= 12
    )
      return;
    const controller = new AbortController();
    activeRequest.current = controller;
    const current = ++generation.current;
    const next: PlanningDialogueMessage[] = [
      ...messages,
      { role: "user", content },
    ];
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const response = await organizationsApi.planningDialogue(
        organizationId,
        workerId,
        goalId,
        {
          worker_version: workerVersion,
          messages: next,
          ...(previousDraft ? { previous_draft: previousDraft } : {}),
        },
        controller.signal,
      );
      if (generation.current !== current) return;
      setMessages([...next, { role: "assistant", content: response.reply }]);
      setResult(response);
      if (response.draft) setPreviousDraft(response.draft);
      setInput("");
    } catch (err) {
      if (generation.current !== current) return;
      setError(apiErrorMessage(err, t("planningDialogue.failed"), t));
    } finally {
      if (generation.current === current) {
        activeRequest.current = null;
        setBusy(false);
      }
    }
  }

  return (
    <>
      <Button onClick={() => setOpen(true)}>
        {t("planningDialogue.open")}
      </Button>
      <Modal
        open={open}
        onCancel={close}
        footer={null}
        title={t("planningDialogue.title")}
        width={720}
      >
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Alert
            type="warning"
            showIcon
            message={t("planningDialogue.disclosure")}
          />
          <Typography.Paragraph type="secondary">
            {t("planningDialogue.serverLimits")}
          </Typography.Paragraph>
          <Typography.Paragraph>
            {t("planningDialogue.goal")}: {goalTitle}
          </Typography.Paragraph>
          <div
            role="log"
            aria-label={t("planningDialogue.history")}
            aria-live="polite"
            style={{ maxHeight: 300, overflowY: "auto" }}
          >
            {messages.map((message, index) => (
              <div key={index}>
                <Typography.Text strong>
                  {t(`planningDialogue.${message.role}`)}
                </Typography.Text>
                <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>
                  {message.content}
                </Typography.Paragraph>
              </div>
            ))}
          </div>
          {error && <Alert type="error" showIcon message={error} />}
          {messages.length >= 12 && (
            <Alert type="info" message={t("planningDialogue.limit")} />
          )}
          <Input.TextArea
            aria-label={t("planningDialogue.input")}
            placeholder={t("planningDialogue.placeholder")}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            maxLength={4000}
            showCount
            rows={3}
            disabled={busy || messages.length >= 12}
          />
          <Button
            type="primary"
            onClick={() => void send()}
            loading={busy}
            disabled={busy || !input.trim() || messages.length >= 12}
          >
            {t("planningDialogue.send")}
          </Button>
          {result?.model_ref && (
            <Typography.Text type="secondary">
              {t("planningDialogue.model")}: {result.model_ref}
            </Typography.Text>
          )}
          {result?.draft && (
            <>
              <Typography.Title level={5}>
                {result.draft.title}
              </Typography.Title>
              <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>
                {result.draft.approach}
              </Typography.Paragraph>
              <ol>
                {result.draft.steps.map((step, index) => (
                  <li key={index}>
                    <strong>{step.action}</strong>
                    <p>{step.expected_output}</p>
                  </li>
                ))}
              </ol>
              <Alert type="info" message={t("planningDialogue.review")} />
              <Button
                onClick={() => {
                  onDraft(result.draft!);
                  close();
                }}
              >
                {t("planningDialogue.useDraft")}
              </Button>
            </>
          )}
        </Space>
      </Modal>
    </>
  );
}
