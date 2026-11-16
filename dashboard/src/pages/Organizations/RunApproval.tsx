import { useEffect, useRef, useState } from "react";
import { Alert, Button, Input, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";
import SensitiveAccessBoundary from "../../components/SensitiveAccessBoundary";
import {
  organizationsApi,
  type EnterpriseSceneRun,
} from "../../api/modules/organizations";

export const approvalMessages = {
  en: {
    open: "Review this run request",
    close: "Close request review",
    notice:
      "Approval can start the submitted work. Review the frozen request and current authorization. This is execution approval, not acceptance of its future report. Closing does not cancel a submitted decision.",
    snapshot: "Submitted execution snapshot",
    prompt: "Submitted instructions",
    requester: "Requested by",
    worker: "Digital worker",
    goal: "Goal",
    criteria: "Success criteria",
    plan: "Plan",
    steps: "Steps",
    sources: "Approved HTTP sources",
    share: "Inspection results shared for review",
    yes: "Yes",
    no: "No",
    note: "Approval / rejection note",
    approve: "Approve this run",
    reject: "Reject this run",
    confirm: "Confirm decision",
    cancel: "Cancel choice",
    failed:
      "Unable to verify the request or decision. Refresh the run status and review again before deciding; no automatic retry.",
    self: "The requester cannot approve or reject their own run.",
    legacy:
      "This request has no complete execution plan. It can be rejected, but must be resubmitted with a ready plan before approval.",
    stale: "This run is no longer awaiting approval. Refresh its status.",
  },
  zh: {
    open: "审阅本次运行申请",
    close: "关闭申请审阅",
    notice:
      "批准可能立即启动申请中的工作。请核对固化申请和当前授权。这是执行审批，不是对未来报告的验收。关闭不会取消已提交的决定。",
    snapshot: "提交时的执行快照",
    prompt: "提交时的执行说明",
    requester: "申请人",
    worker: "数字员工",
    goal: "目标",
    criteria: "成功标准",
    plan: "方案",
    steps: "步骤",
    sources: "申请的 HTTP 来源",
    share: "巡检结果共享供审阅",
    yes: "是",
    no: "否",
    note: "批准／拒绝说明",
    approve: "批准本次运行",
    reject: "拒绝本次运行",
    confirm: "确认决定",
    cancel: "取消选择",
    failed:
      "无法核实申请或决定。请先刷新运行状态并重新审阅，再作决定；不会自动重试。",
    self: "申请人不能批准或拒绝自己的运行。",
    legacy: "此申请缺少完整执行方案，可以拒绝；批准前需携带就绪方案重新申请。",
    stale: "此运行已不再等待审批，请刷新状态。",
  },
};
interface Props {
  organizationId: string;
  runId: string;
  currentUserId: number;
  onDecided: () => void;
}
export default function RunApproval(props: Props) {
  return (
    <SensitiveAccessBoundary>
      <ApprovalSession
        key={JSON.stringify([
          props.organizationId,
          props.runId,
          props.currentUserId,
        ])}
        {...props}
      />
    </SensitiveAccessBoundary>
  );
}
function ApprovalSession({
  organizationId,
  runId,
  currentUserId,
  onDecided,
}: Props) {
  const { i18n } = useTranslation();
  const m =
    approvalMessages[
      (i18n.resolvedLanguage || i18n.language || "en").startsWith("zh")
        ? "zh"
        : "en"
    ];
  const generation = useRef(0);
  const busy = useRef(false);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [run, setRun] = useState<EnterpriseSceneRun>();
  const [note, setNote] = useState("");
  const [choice, setChoice] = useState<"approve" | "reject">();
  useEffect(
    () => () => {
      generation.current++;
    },
    [],
  );
  const matches = (r: EnterpriseSceneRun) =>
    r &&
    r.organization_id === organizationId &&
    r.run_id === runId &&
    Number.isSafeInteger(r.requested_by) &&
    r.requested_by > 0 &&
    typeof r.prompt_snapshot === "string";
  const clear = () => {
    generation.current++;
    busy.current = false;
    setLoading(false);
    setRun(undefined);
    setNote("");
    setChoice(undefined);
    setFailed(false);
  };
  const review = async () => {
    if (busy.current) return;
    clear();
    setOpen(true);
    setLoading(true);
    busy.current = true;
    const seq = ++generation.current;
    try {
      const result = await organizationsApi.sceneRun(organizationId, runId);
      if (!matches(result)) throw new Error("Invalid request");
      if (generation.current === seq) setRun(result);
    } catch {
      if (generation.current === seq) setFailed(true);
    } finally {
      if (generation.current === seq) {
        busy.current = false;
        setLoading(false);
      }
    }
  };
  const independent = run?.requested_by !== currentUserId;
  const ready = !!(
    run?.plan_id &&
    run.plan_snapshot?.worker?.worker_id &&
    run.plan_snapshot.capabilities &&
    run.plan_snapshot.plan?.plan_id === run.plan_id
  );
  const decide = async () => {
    if (
      busy.current ||
      !run ||
      !choice ||
      !independent ||
      run.status !== "awaiting_approval" ||
      (choice === "approve" && !ready)
    )
      return;
    busy.current = true;
    setLoading(true);
    const seq = ++generation.current;
    try {
      const result = await organizationsApi.decideSceneRun(
        organizationId,
        runId,
        choice,
        note.trim(),
      );
      if (
        !matches(result) ||
        result.requested_by !== run.requested_by ||
        result.approved_by !== currentUserId ||
        !(choice === "reject"
          ? result.status === "rejected"
          : ["queued", "succeeded", "failed"].includes(result.status))
      )
        throw new Error("Invalid decision");
      if (generation.current === seq) {
        clear();
        setOpen(false);
        onDecided();
      }
    } catch {
      if (generation.current === seq) {
        setRun(undefined);
        setChoice(undefined);
        setNote("");
        setFailed(true);
      }
    } finally {
      if (generation.current === seq) {
        busy.current = false;
        setLoading(false);
      }
    }
  };
  return (
    <section aria-label={m.open}>
      {!open ? (
        <Button onClick={() => void review()}>{m.open}</Button>
      ) : (
        <Space direction="vertical" style={{ width: "100%" }}>
          <Alert type="warning" message={m.notice} />
          <Button
            onClick={() => {
              clear();
              setOpen(false);
            }}
          >
            {m.close}
          </Button>
          {loading && <Typography.Text role="status">…</Typography.Text>}
          {failed && <Alert type="error" message={m.failed} />}
          {run && (
            <>
              <Typography.Text>
                {m.requester}: {run.requested_by}
              </Typography.Text>
              <Typography.Title level={5}>{m.prompt}</Typography.Title>
              <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>
                {run.prompt_snapshot}
              </Typography.Paragraph>
              <Typography.Title level={5}>{m.snapshot}</Typography.Title>
              {run.plan_snapshot && (
                <>
                  <p>
                    {m.worker}: {run.plan_snapshot.worker?.name} (
                    {run.plan_snapshot.worker?.worker_id})
                  </p>
                  <p>
                    {m.goal}: {run.plan_snapshot.goal?.title}
                  </p>
                  <p>
                    {m.criteria}: {run.plan_snapshot.goal?.success_criteria}
                  </p>
                  <p>
                    {m.plan}: {run.plan_snapshot.plan?.title} (v
                    {run.plan_snapshot.plan?.version})
                  </p>
                  <p style={{ whiteSpace: "pre-wrap" }}>
                    {run.plan_snapshot.plan?.approach}
                  </p>
                  <ol aria-label={m.steps}>
                    {run.plan_snapshot.plan?.steps?.map((step, index) => (
                      <li key={index}>
                        {step.action} → {step.expected_output}
                      </li>
                    ))}
                  </ol>
                  {run.plan_snapshot.inspection && (
                    <section aria-label={m.sources}>
                      <p>
                        {m.share}:{" "}
                        {run.plan_snapshot.inspection.share_results
                          ? m.yes
                          : m.no}
                      </p>
                      <ul>
                        {run.plan_snapshot.inspection.sources.map((source) => (
                          <li key={source.job_id}>
                            {source.name} ({source.job_id}, v
                            {source.job_version}; {source.connection_id}, v
                            {source.connection_version})
                          </li>
                        ))}
                      </ul>
                    </section>
                  )}
                </>
              )}
              <Typography.Text>
                MCP: {run.mcp_servers?.join(", ") || "—"}
              </Typography.Text>
              {run.status !== "awaiting_approval" ? (
                <Alert message={m.stale} />
              ) : !independent ? (
                <Alert message={m.self} />
              ) : (
                <>
                  {!ready && <Alert type="warning" message={m.legacy} />}
                  <Input.TextArea
                    aria-label={m.note}
                    maxLength={2000}
                    value={note}
                    disabled={loading}
                    onChange={(e) => {
                      setNote(e.target.value);
                      setChoice(undefined);
                    }}
                  />
                  <Button
                    disabled={loading || !ready}
                    onClick={() => setChoice("approve")}
                  >
                    {m.approve}
                  </Button>
                  <Button
                    disabled={loading}
                    onClick={() => setChoice("reject")}
                  >
                    {m.reject}
                  </Button>
                  {choice && (
                    <Space
                      role="group"
                      aria-label={choice === "approve" ? m.approve : m.reject}
                    >
                      <Button loading={loading} onClick={() => void decide()}>
                        {m.confirm}
                      </Button>
                      <Button
                        disabled={loading}
                        onClick={() => setChoice(undefined)}
                      >
                        {m.cancel}
                      </Button>
                    </Space>
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
