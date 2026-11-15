import { useEffect, useRef, useState } from "react";
import { Alert, Button, Checkbox, Space, Spin, Typography } from "antd";
import { useTranslation } from "react-i18next";
import {
  inspectionRunApi,
  type InspectionJobOption,
} from "../../api/modules/inspectionRun";

export interface InspectionRunValue {
  target?: string;
  enabled: boolean;
  jobIds: string[];
  share: boolean;
}
export const emptyInspectionRun: InspectionRunValue = {
  enabled: false,
  jobIds: [],
  share: false,
};
export const inspectionRunMessages = {
  en: {
    mode: "Use a one-time HTTP inspection instead of the Agent",
    hint: "Only your configured HTTP inspection tasks are listed. After independent approval, the selected endpoints will be read once and a local report created from this run. No LLM, memory, automatic repair or message delivery is used. Select up to 10 sources.",
    share:
      "I agree to share this run's inspection results and report with authorized scene readers and independent reviewers.",
    failed:
      "Could not load inspection tasks. Retry or use the ordinary Agent mode.",
    empty:
      "No eligible HTTP inspection tasks. Configure your own task for this scene's Agent in Enterprise Assistant first.",
    retry: "Reload inspection tasks",
    required:
      "Select 1–10 inspection tasks and confirm sharing before requesting approval.",
    sources: "Inspection sources",
    modeSummary: "One-time HTTP inspection · no LLM or outbound messages",
  },
  zh: {
    mode: "改用单次 HTTP 巡检（不调用智能体）",
    hint: "仅列出你配置的 HTTP 巡检任务。独立审批后，将读取选定端点一次，并以本次采集生成本地报告。不调用模型、记忆、自动修复或消息发送。最多选择 10 个来源。",
    share: "我同意将本次巡检结果和报告共享给有权读取场景的用户及独立验收人。",
    failed: "无法加载巡检任务。请重试或使用普通智能体模式。",
    empty:
      "没有符合条件的 HTTP 巡检任务。请先在企业助手中为此场景的智能体配置你自己的任务。",
    retry: "重新加载巡检任务",
    required: "请选择 1–10 个巡检任务并确认共享后再申请审批。",
    sources: "巡检来源",
    modeSummary: "单次 HTTP 巡检 · 无模型调用或外发消息",
  },
};

export default function InspectionRunOptions({
  organizationId,
  sceneId,
  value = emptyInspectionRun,
  onChange,
  disabled = false,
}: {
  organizationId: string;
  sceneId: string;
  value?: InspectionRunValue;
  onChange?: (value: InspectionRunValue) => void;
  disabled?: boolean;
}) {
  const { i18n } = useTranslation();
  const m = inspectionRunMessages[i18n.language.startsWith("zh") ? "zh" : "en"];
  const [jobs, setJobs] = useState<InspectionJobOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const sequence = useRef(0);
  useEffect(
    () => () => {
      sequence.current++;
    },
    [],
  );
  async function load() {
    const seq = ++sequence.current;
    setJobs([]);
    setLoading(true);
    setFailed(false);
    onChange?.({
      enabled: true,
      jobIds: [],
      share: false,
      target: `${organizationId}:${sceneId}`,
    });
    try {
      const data = await inspectionRunApi.candidates(organizationId, sceneId);
      if (seq === sequence.current) setJobs(data);
    } catch {
      if (seq === sequence.current) setFailed(true);
    } finally {
      if (seq === sequence.current) setLoading(false);
    }
  }
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Checkbox
        checked={value.enabled}
        disabled={disabled || !sceneId}
        onChange={(event) => {
          if (event.target.checked) void load();
          else {
            sequence.current++;
            setLoading(false);
            setJobs([]);
            onChange?.(emptyInspectionRun);
          }
        }}
      >
        {m.mode}
      </Checkbox>
      {value.enabled && (
        <>
          <Alert type="info" message={m.hint} />
          {loading ? (
            <Spin />
          ) : failed ? (
            <Alert type="error" message={m.failed} />
          ) : jobs.length === 0 ? (
            <Typography.Paragraph>{m.empty}</Typography.Paragraph>
          ) : (
            <Space direction="vertical" role="group" aria-label={m.sources}>
              {jobs.map((job) => (
                <Checkbox
                  key={job.job_id}
                  checked={value.jobIds.includes(job.job_id)}
                  disabled={
                    disabled ||
                    (!value.jobIds.includes(job.job_id) &&
                      value.jobIds.length >= 10)
                  }
                  onChange={(event) =>
                    onChange?.({
                      ...value,
                      jobIds: event.target.checked
                        ? [...value.jobIds, job.job_id]
                        : value.jobIds.filter((id) => id !== job.job_id),
                      share: false,
                    })
                  }
                >
                  {job.name} ({job.job_id}, v{job.job_version}/
                  {job.connection_version})
                </Checkbox>
              ))}
            </Space>
          )}
          <Button disabled={disabled || loading} onClick={() => void load()}>
            {m.retry}
          </Button>
          <Checkbox
            checked={value.share}
            disabled={
              disabled || loading || failed || value.jobIds.length === 0
            }
            onChange={(event) =>
              onChange?.({ ...value, share: event.target.checked })
            }
          >
            {m.share}
          </Checkbox>
        </>
      )}
    </Space>
  );
}
