import { useEffect, useRef, useState } from "react";
import { Alert, Button, Input, Space } from "antd";
import { useTranslation } from "react-i18next";
import SensitiveAccessBoundary from "../../components/SensitiveAccessBoundary";
import {
  organizationsApi,
  type DigitalWorkerPlan,
  type EnterpriseSceneRun,
} from "../../api/modules/organizations";
import {
  deliveryAcceptanceApi,
  validAcceptanceRecord,
  type AcceptanceTarget,
} from "../../api/modules/deliveryAcceptance";
import InspectionRunOptions, {
  emptyInspectionRun,
  type InspectionRunValue,
} from "./InspectionRunOptions";

export const revisionMessages = {
  en: {
    open: "Revise and request another HTTP inspection",
    openNew: "Open the new run",
    close: "Close revision",
    notice:
      "Create a separate draft from the reviewed snapshot. Each save, readiness change and new request is explicit; another independent approval is required. Old decisions stay unchanged. Closing does not cancel submitted writes; check the goal's plans and run list after an uncertain result.",
    unavailable:
      "Revision unavailable or result uncertain. Check current permissions, goal, plans and runs before submitting again.",
    title: "Revision plan title",
    approach: "Revised approach",
    action: "Step action",
    output: "Expected output",
    add: "Add step",
    remove: "Remove step",
    create: "Save as a new draft",
    ready: "Mark new draft ready",
    request: "Request independent approval for new inspection",
    draft: "New plan",
    submitted: "New run awaiting independent approval",
    source: "Original run",
    feedback: "Reviewer feedback",
    noNote: "No feedback recorded.",
  },
  zh: {
    open: "修订并重新申请 HTTP 巡检",
    openNew: "打开新的运行申请",
    close: "关闭修订",
    notice:
      "从原验收快照创建独立草稿。保存、标记就绪和新申请均需明确操作，并重新独立审批；旧决定不变。关闭不会取消已提交的写入；结果不确定时先到目标方案和运行列表核对。",
    unavailable:
      "无法修订或结果不确定。再次提交前请核对当前权限、目标、方案和运行记录。",
    title: "修订方案标题",
    approach: "修订方案说明",
    action: "步骤动作",
    output: "预期结果",
    add: "添加步骤",
    remove: "移除步骤",
    create: "保存为新草稿",
    ready: "将新草稿标记就绪",
    request: "为新巡检申请独立审批",
    draft: "新方案",
    submitted: "新运行等待独立审批",
    source: "原运行",
    feedback: "验收意见",
    noNote: "未记录验收意见。",
  },
};
export const failureFollowUpMessages = {
  en: {
    open: "Request a new HTTP inspection after failure",
    notice:
      "Check the source tasks and permissions before submitting again. Create a new draft from the failed run; select current sources and consent to sharing again. A new independent approval is required. The old run stays failed and may have collected partial results. Nothing is automatically retried; closing does not cancel submitted writes.",
    title: "New inspection plan title",
    approach: "Updated inspection approach",
  },
  zh: {
    open: "从失败记录重新申请 HTTP 巡检",
    notice:
      "再次提交前请检查来源任务与权限。从失败运行创建新草稿，重新选择当前来源并确认共享，仍需独立审批。旧运行保持失败，之前可能已经采集部分结果。不会自动重试；关闭不会取消已提交的写入。",
    title: "新的巡检方案标题",
    approach: "调整后的巡检方案说明",
  },
};
interface Props {
  target: AcceptanceTarget;
  currentUserId: number;
  fromFailure?: boolean;
  onOpenRun?: (runId: string) => void;
}
export default function InspectionRevision(props: Props) {
  return (
    <SensitiveAccessBoundary>
      <RevisionSession key={JSON.stringify(props)} {...props} />
    </SensitiveAccessBoundary>
  );
}
function RevisionSession({
  target,
  currentUserId,
  fromFailure = false,
  onOpenRun,
}: Props) {
  const { i18n } = useTranslation();
  const language = (i18n.resolvedLanguage || i18n.language || "en").startsWith(
    "zh",
  )
    ? "zh"
    : "en";
  const m = {
    ...revisionMessages[language],
    ...(fromFailure ? failureFollowUpMessages[language] : {}),
  };
  const [source, setSource] = useState<EnterpriseSceneRun>();
  const [note, setNote] = useState("");
  const [title, setTitle] = useState("");
  const [approach, setApproach] = useState("");
  const [steps, setSteps] = useState<DigitalWorkerPlan["steps"]>([]);
  const [plan, setPlan] = useState<DigitalWorkerPlan>();
  const [inspection, setInspection] =
    useState<InspectionRunValue>(emptyInspectionRun);
  const [submitted, setSubmitted] = useState<string>();
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const generation = useRef(0);
  const busy = useRef(false);
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
    setSource(undefined);
    setPlan(undefined);
    setSubmitted(undefined);
    setNote("");
    setTitle("");
    setApproach("");
    setSteps([]);
    setInspection(emptyInspectionRun);
  };
  const perform = async (operation: (seq: number) => Promise<void>) => {
    if (busy.current || failed) return;
    busy.current = true;
    setLoading(true);
    const seq = ++generation.current;
    try {
      await operation(seq);
    } catch {
      if (seq === generation.current) setFailed(true);
    } finally {
      if (seq === generation.current) {
        busy.current = false;
        setLoading(false);
      }
    }
  };
  const load = () =>
    perform(async (seq) => {
      const [run, feedback] = await Promise.all([
        organizationsApi.sceneRun(target.organizationId, target.runId),
        fromFailure
          ? Promise.resolve(null)
          : deliveryAcceptanceApi.feedback(target),
      ]);
      const snapshot = run.plan_snapshot;
      if (
        run.run_id !== target.runId ||
        run.organization_id !== target.organizationId ||
        run.requested_by !== currentUserId ||
        run.status !== (fromFailure ? "failed" : "succeeded") ||
        snapshot?.inspection?.mode !== "http_inspection" ||
        !snapshot.plan ||
        !snapshot.goal ||
        !snapshot.worker ||
        run.plan_id !== snapshot.plan.plan_id ||
        (!fromFailure &&
          (!feedback ||
            !validAcceptanceRecord(feedback, target) ||
            feedback.decision !== "changes_requested"))
      )
        throw new Error("Unavailable source");
      const [workers, goals] = await Promise.all([
        organizationsApi.digitalWorkers(target.organizationId),
        organizationsApi.digitalWorkerGoals(
          target.organizationId,
          snapshot.worker.worker_id,
        ),
      ]);
      if (
        !workers.some(
          (w) =>
            w.worker_id === snapshot.worker.worker_id &&
            w.organization_id === target.organizationId &&
            w.status === "active",
        ) ||
        !goals.some(
          (g) =>
            g.goal_id === snapshot.goal.goal_id &&
            g.worker_id === snapshot.worker.worker_id &&
            g.status === "active",
        )
      )
        throw new Error("Inactive target");
      if (seq === generation.current) {
        setSource(run);
        setNote(feedback?.review_note || "");
        setTitle(snapshot.plan.title);
        setApproach(snapshot.plan.approach);
        setSteps(snapshot.plan.steps.map((s) => ({ ...s })));
      }
    });
  const create = () =>
    perform(async (seq) => {
      if (!source?.plan_snapshot) throw new Error("No source");
      const snapshot = source.plan_snapshot;
      const created = await organizationsApi.createDigitalWorkerPlan(
        target.organizationId,
        snapshot.worker.worker_id,
        snapshot.goal.goal_id,
        {
          title: title.trim(),
          approach: approach.trim(),
          steps: steps.map((s) => ({
            action: s.action.trim(),
            expected_output: s.expected_output.trim(),
          })),
        },
      );
      if (
        !created.plan_id ||
        created.plan_id === source.plan_id ||
        created.goal_id !== snapshot.goal.goal_id ||
        created.status !== "draft"
      )
        throw new Error("Invalid draft");
      if (seq === generation.current) setPlan(created);
    });
  const ready = () =>
    perform(async (seq) => {
      if (!source?.plan_snapshot || !plan) throw new Error("No draft");
      const snapshot = source.plan_snapshot;
      const updated = await organizationsApi.updateDigitalWorkerPlan(
        target.organizationId,
        snapshot.worker.worker_id,
        snapshot.goal.goal_id,
        plan.plan_id,
        plan.version,
        { status: "ready" },
      );
      if (
        updated.plan_id !== plan.plan_id ||
        updated.goal_id !== plan.goal_id ||
        updated.status !== "ready" ||
        updated.version <= plan.version
      )
        throw new Error("Invalid ready plan");
      if (seq === generation.current) setPlan(updated);
    });
  const request = () =>
    perform(async (seq) => {
      if (
        !source ||
        !plan ||
        plan.status !== "ready" ||
        !inspection.enabled ||
        !inspection.share ||
        !inspection.jobIds.length ||
        inspection.target !== `${target.organizationId}:${source.scene_id}`
      )
        throw new Error("Invalid request");
      const run = await organizationsApi.requestSceneRun(
        target.organizationId,
        source.scene_id,
        plan.plan_id,
        { useMemory: false, captureMemory: false },
        { jobIds: inspection.jobIds, share: true },
      );
      if (
        run.run_id === source.run_id ||
        !run.run_id ||
        run.organization_id !== target.organizationId ||
        run.scene_id !== source.scene_id ||
        run.plan_id !== plan.plan_id ||
        run.status !== "awaiting_approval"
      )
        throw new Error("Invalid new run");
      if (seq === generation.current) setSubmitted(run.run_id);
    });
  const validDraft =
    !!title.trim() &&
    steps.length > 0 &&
    steps.length <= 30 &&
    steps.every((s) => s.action.trim() && s.expected_output.trim());
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      {!source && !failed && (
        <Button loading={loading} onClick={() => void load()}>
          {m.open}
        </Button>
      )}
      {(source || failed || loading) && (
        <>
          <Alert type="info" message={m.notice} />
          <Button onClick={close}>{m.close}</Button>
        </>
      )}
      {failed && <Alert type="error" message={m.unavailable} />}
      {source && (
        <>
          <p>
            {m.source}: {source.run_id}
          </p>
          {!fromFailure && (
            <p style={{ whiteSpace: "pre-wrap" }}>
              {m.feedback}: {note || m.noNote}
            </p>
          )}
          {!plan && (
            <>
              <Input
                aria-label={m.title}
                maxLength={200}
                value={title}
                disabled={loading || failed}
                onChange={(e) => setTitle(e.target.value)}
              />
              <Input.TextArea
                aria-label={m.approach}
                maxLength={4000}
                value={approach}
                disabled={loading || failed}
                onChange={(e) => setApproach(e.target.value)}
              />
              {steps.map((step, index) => (
                <Space key={index} direction="vertical">
                  <Input
                    aria-label={`${m.action} ${index + 1}`}
                    maxLength={500}
                    value={step.action}
                    disabled={loading || failed}
                    onChange={(e) =>
                      setSteps(
                        steps.map((s, i) =>
                          i === index ? { ...s, action: e.target.value } : s,
                        ),
                      )
                    }
                  />
                  <Input
                    aria-label={`${m.output} ${index + 1}`}
                    maxLength={1000}
                    value={step.expected_output}
                    disabled={loading || failed}
                    onChange={(e) =>
                      setSteps(
                        steps.map((s, i) =>
                          i === index
                            ? { ...s, expected_output: e.target.value }
                            : s,
                        ),
                      )
                    }
                  />
                  <Button
                    disabled={loading || failed || steps.length === 1}
                    onClick={() =>
                      setSteps(steps.filter((_, i) => i !== index))
                    }
                  >
                    {m.remove} {index + 1}
                  </Button>
                </Space>
              ))}
              <Button
                disabled={loading || failed || steps.length >= 30}
                onClick={() =>
                  setSteps([...steps, { action: "", expected_output: "" }])
                }
              >
                {m.add}
              </Button>
              <Button
                disabled={loading || failed || !validDraft}
                onClick={() => void create()}
              >
                {m.create}
              </Button>
            </>
          )}
          {plan && (
            <p>
              {m.draft}: {plan.plan_id} · {plan.status}
            </p>
          )}
          {plan?.status === "draft" && (
            <Button disabled={loading || failed} onClick={() => void ready()}>
              {m.ready}
            </Button>
          )}
          {plan?.status === "ready" && !submitted && (
            <>
              <InspectionRunOptions
                organizationId={target.organizationId}
                sceneId={source.scene_id}
                value={inspection}
                onChange={setInspection}
                disabled={loading || failed}
              />
              <Button
                disabled={
                  loading ||
                  failed ||
                  !inspection.enabled ||
                  !inspection.share ||
                  !inspection.jobIds.length
                }
                onClick={() => void request()}
              >
                {m.request}
              </Button>
            </>
          )}
          {submitted && (
            <>
              <Alert type="success" message={`${m.submitted}: ${submitted}`} />
              {onOpenRun && (
                <Button onClick={() => onOpenRun(submitted)}>
                  {m.openNew}
                </Button>
              )}
            </>
          )}
        </>
      )}
    </Space>
  );
}
