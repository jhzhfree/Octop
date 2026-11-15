import { useEffect, useRef, useState } from "react";
import { Alert, Button, Modal, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";
import type { DigitalWorkerGoal } from "../../api/modules/organizations";

interface Props {
  goal: DigitalWorkerGoal;
  disabled?: boolean;
  onChange: () => Promise<void>;
  onReload?: () => Promise<void>;
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
        props.goal.version,
        props.disabled,
      ])}
      {...props}
    />
  );
}

function GoalActionSession({ goal, disabled, onChange, onReload }: Props) {
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
    if (disabled || busy.current || (failed && onReload)) return;
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
  const reload = async () => {
    if (!onReload || busy.current || disabled) return;
    busy.current = true;
    setLoading(true);
    try {
      await onReload();
      if (mounted.current) {
        setFailed(false);
        setOpen(false);
      }
    } catch {
      if (mounted.current) setFailed(true);
    } finally {
      if (mounted.current) {
        busy.current = false;
        setLoading(false);
      }
    }
  };
  const failure = (
    <Space direction="vertical">
      <Alert
        type="error"
        message={t(
          onReload ? "goalLifecycle.reloadRequired" : "goalLifecycle.failed",
        )}
      />
      {onReload && (
        <Button
          loading={loading}
          disabled={disabled}
          onClick={() => void reload()}
        >
          {t("goalLifecycle.reload")}
        </Button>
      )}
    </Space>
  );
  return (
    <>
      <Button
        disabled={disabled || (!!onReload && failed)}
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
      {failed && !open && failure}
      <Modal
        open={open && !disabled}
        title={t("goalLifecycle.confirmTitle")}
        okText={t("goalLifecycle.confirm")}
        cancelText={t("common.cancel")}
        confirmLoading={loading}
        okButtonProps={{
          disabled: disabled || loading || (!!onReload && failed),
        }}
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
          {failed && failure}
        </Space>
      </Modal>
    </>
  );
}
