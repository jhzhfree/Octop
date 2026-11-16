import { useEffect, useRef, useState } from "react";
import { Alert, Button, Input, Modal, Select, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";
import SensitiveAccessBoundary from "../../components/SensitiveAccessBoundary";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import {
  organizationsApi,
  type DigitalWorker,
  type DigitalWorkerGoal,
} from "../../api/modules/organizations";
import {
  goalHandoffsApi,
  type GoalHandoff,
  type HandoffBody,
} from "../../api/modules/goalHandoffs";
interface Props {
  organizationId: string;
  canManage: boolean;
  source?: { worker: DigitalWorker; goal: DigitalWorkerGoal };
  onClose?: () => void;
  onOpenWorker: (id: string) => void;
}
export default function GoalHandoffs(props: Props) {
  const user = useCurrentUser();
  return (
    <SensitiveAccessBoundary>
      <Session
        key={JSON.stringify([
          props.organizationId,
          props.canManage,
          user?.id,
          user?.role,
          user?.permissions,
          props.source?.goal.goal_id,
          props.source?.goal.version,
          props.source?.worker.version,
        ])}
        {...props}
      />
    </SensitiveAccessBoundary>
  );
}
function Session({
  organizationId,
  canManage,
  source,
  onClose,
  onOpenWorker,
}: Props) {
  const { i18n } = useTranslation();
  const zh = (i18n.resolvedLanguage || i18n.language || "en").startsWith("zh");
  const label = (cn: string, en: string) => (zh ? cn : en);
  const [open, setOpen] = useState(false);
  const [workers, setWorkers] = useState<DigitalWorker[]>([]);
  const [rows, setRows] = useState<GoalHandoff[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [target, setTarget] = useState<string>();
  const [reason, setReason] = useState("");
  const [title, setTitle] = useState(source?.goal.title || "");
  const [criteria, setCriteria] = useState(source?.goal.success_criteria || "");
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [done, setDone] = useState<GoalHandoff>();
  const intent = useRef<HandoffBody>();
  const generation = useRef(0);
  const busy = useRef(false);
  useEffect(
    () => () => {
      generation.current++;
    },
    [],
  );
  if (!canManage) return null;
  const creatable =
    source?.worker.status === "active" &&
    ["active", "completed"].includes(source.goal.status);
  const load = async (before?: string) => {
    if (busy.current) return;
    const token = ++generation.current;
    busy.current = true;
    setOpen(true);
    setLoading(true);
    setError(false);
    setRows([]);
    try {
      const [history, choices] = await Promise.all([
        goalHandoffsApi.list(organizationId, source?.goal.goal_id, before),
        creatable
          ? organizationsApi.digitalWorkers(organizationId)
          : Promise.resolve([]),
      ]);
      if (token !== generation.current) return;
      if (history.items.some((row) => row.organization_id !== organizationId))
        throw new Error("Wrong organization");
      setRows(history.items);
      setNext(history.next_before);
      setWorkers(
        choices.filter(
          (w) =>
            w.organization_id === organizationId &&
            w.status === "active" &&
            w.position_id !== source?.worker.position_id,
        ),
      );
    } catch {
      if (token === generation.current) setError(true);
    } finally {
      if (token === generation.current) {
        busy.current = false;
        setLoading(false);
      }
    }
  };
  const submit = async () => {
    if (busy.current || !source || !creatable || done) return;
    const receiver = workers.find((w) => w.worker_id === target);
    if (!intent.current) {
      if (!receiver || !reason.trim() || !title.trim() || !criteria.trim())
        return;
      intent.current = {
        request_id: crypto.randomUUID(),
        expected_source_version: source.goal.version,
        expected_target_version: receiver.version,
        target_worker_id: receiver.worker_id,
        reason,
        title,
        description: "",
        success_criteria: criteria,
      };
    }
    const token = ++generation.current;
    busy.current = true;
    setLoading(true);
    setError(false);
    setSubmitted(true);
    try {
      const result = await goalHandoffsApi.create(
        organizationId,
        source.worker.worker_id,
        source.goal.goal_id,
        intent.current,
      );
      if (token === generation.current) setDone(result);
    } catch {
      if (token === generation.current) setError(true);
    } finally {
      if (token === generation.current) {
        busy.current = false;
        setLoading(false);
      }
    }
  };
  const close = (refresh = true) => {
    generation.current++;
    busy.current = false;
    setOpen(false);
    setLoading(false);
    setRows([]);
    setWorkers([]);
    setError(false);
    // Retain an uncertain request key until this goal's authoritative revision changes.
    if (refresh) onClose?.();
  };
  return (
    <>
      <Button onClick={() => void load()}>
        {label(
          source ? "岗位交接" : "交接记录",
          source ? "Role handoff" : "Handoff history",
        )}
      </Button>
      <Modal
        open={open}
        title={label("岗位交接", "Role handoff")}
        footer={null}
        onCancel={() => close()}
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Alert
            type="info"
            message={label(
              "创建接收岗位的目标草稿，不执行任务、不转移权限。原目标状态不变；接收目标仍须确认、规划和审批。仅支持 SQLite。",
              "Creates a receiving role's draft goal; no execution or permission transfer. The source status stays unchanged. Confirm, plan and approve the receiving goal using the existing workflow. SQLite only.",
            )}
          />
          {error && (
            <Alert
              type="error"
              message={label(
                "读取或交接失败，结果可能未知。重试复用原请求；版本或权限冲突需关闭并刷新，先核对记录再重新交接。",
                "Read or handoff failed; outcome may be unknown. Retry reuses the original request. For version or access conflicts, close and refresh; check history before creating another handoff.",
              )}
            />
          )}
          {creatable && !done && (
            <>
              <Select
                aria-label="Receiving role"
                style={{ width: "100%" }}
                value={target}
                disabled={submitted || loading}
                onChange={setTarget}
                placeholder={label(
                  "选择接收岗位员工",
                  "Select receiving worker",
                )}
                options={workers.map((w) => ({
                  value: w.worker_id,
                  label: `${w.name} · ${w.position_id}`,
                }))}
              />
              <Input
                aria-label="Handoff reason"
                placeholder={label("交接理由", "Handoff reason")}
                value={reason}
                maxLength={2000}
                disabled={submitted}
                onChange={(e) => setReason(e.target.value)}
              />
              <Input
                aria-label="Receiving goal title"
                value={title}
                maxLength={200}
                disabled={submitted}
                onChange={(e) => setTitle(e.target.value)}
              />
              <Input.TextArea
                aria-label="Receiving success criteria"
                value={criteria}
                maxLength={4000}
                disabled={submitted}
                onChange={(e) => setCriteria(e.target.value)}
              />
              <Button
                type="primary"
                loading={loading}
                disabled={
                  !target || !reason.trim() || !title.trim() || !criteria.trim()
                }
                onClick={() => void submit()}
              >
                {label(
                  submitted ? "重试原交接" : "确认创建接收目标草稿",
                  submitted
                    ? "Retry original handoff"
                    : "Confirm receiving draft",
                )}
              </Button>
            </>
          )}
          {done && (
            <Alert
              type="success"
              message={label("交接已记录", "Handoff recorded")}
            />
          )}
          {(done ? [done] : rows).map((row) => (
            <div key={row.handoff_id}>
              <Typography.Text strong>
                {row.source_snapshot.worker_name} →{" "}
                {row.target_snapshot.position_title} ·{" "}
                {row.target_snapshot.name}
              </Typography.Text>
              <Typography.Paragraph>{row.reason}</Typography.Paragraph>
              <Typography.Paragraph>
                {label("发起账号", "Initiating account")} #{row.created_by} ·{" "}
                {row.handoff_id}
              </Typography.Paragraph>
              <Typography.Paragraph>
                {row.source_snapshot.title} → {row.target_goal_id} ·{" "}
                {row.target_goal_status ?? label("目标已移除", "Goal removed")}
              </Typography.Paragraph>
              <Button
                disabled={row.target_goal_status == null}
                onClick={() => {
                  close(false);
                  onOpenWorker(row.target_worker_id);
                }}
              >
                {label(
                  "打开接收员工的目标与计划",
                  "Open receiving worker's goals and plans",
                )}
              </Button>
            </div>
          ))}
          {!done && (
            <Button loading={loading} onClick={() => void load()}>
              {label("刷新记录", "Refresh history")}
            </Button>
          )}
          {!done && next && (
            <Button loading={loading} onClick={() => void load(next)}>
              {label("下一页", "Next page")}
            </Button>
          )}
        </Space>
      </Modal>
    </>
  );
}
