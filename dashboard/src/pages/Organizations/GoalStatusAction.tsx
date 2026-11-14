import { useEffect, useRef, useState } from "react";
import { Alert, Button, Modal, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";
import type { DigitalWorkerGoal } from "../../api/modules/organizations";

interface Props {
  goal: DigitalWorkerGoal;
  disabled?: boolean;
  onChange: () => Promise<void>;
}

/** Reset confirmation when its goal, revision or permission changes. */
export default function GoalStatusAction(props: Props) {
  return (
    <GoalActionSession
      key={JSON.stringify([
        props.goal.worker_id,
        props.goal.goal_id,
        props.goal.status,
        props.goal.updated_at,
        props.disabled,
      ])}
      {...props}
    />
  );
}

function GoalActionSession({ goal, disabled, onChange }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const busy = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  if (goal.status !== "draft" && goal.status !== "active") return null;
  const submit = async () => {
    if (disabled || busy.current) return;
    busy.current = true;
    setLoading(true);
    setFailed(false);
    try {
      await onChange();
      if (mounted.current) setOpen(false);
    } catch {
      if (mounted.current) setFailed(true);
    } finally {
      if (mounted.current) {
        busy.current = false;
        setLoading(false);
      }
    }
  };
  return (
    <>
      <Button
        disabled={disabled}
        loading={loading}
        onClick={() => {
          if (busy.current) return;
          setFailed(false);
          if (goal.status === "active") setOpen(true);
          else void submit();
        }}
      >
        {t(
          goal.status === "draft"
            ? "organizations.activateGoal"
            : "organizations.completeGoal",
        )}
      </Button>
      {failed && !open && (
        <Alert type="error" message={t("goalLifecycle.failed")} />
      )}
      <Modal
        open={open && !disabled}
        title={t("goalLifecycle.confirmTitle")}
        okText={t("goalLifecycle.confirm")}
        cancelText={t("common.cancel")}
        confirmLoading={loading}
        okButtonProps={{ disabled: disabled || loading }}
        cancelButtonProps={{ disabled: loading }}
        closable={!loading}
        maskClosable={!loading}
        keyboard={!loading}
        onOk={() => void submit()}
        onCancel={() => {
          if (!busy.current) {
            setOpen(false);
            setFailed(false);
          }
        }}
      >
        <Space direction="vertical">
          <Typography.Text strong>{goal.title}</Typography.Text>
          <Typography.Text>
            {t("organizations.successCriteria")}
          </Typography.Text>
          <Typography.Paragraph>
            {goal.success_criteria || t("goalLifecycle.noCriteria")}
          </Typography.Paragraph>
          <Alert type="warning" message={t("goalLifecycle.notice")} />
          {failed && <Alert type="error" message={t("goalLifecycle.failed")} />}
        </Space>
      </Modal>
    </>
  );
}
