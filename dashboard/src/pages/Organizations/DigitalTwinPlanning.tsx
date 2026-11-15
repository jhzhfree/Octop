import { Alert, Button, Form, Input, Modal, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { DigitalWorkerPlan } from "../../api/modules/organizations";
import {
  buildDigitalTwinPlanDraft,
  type DigitalTwinPlanningIntake,
} from "./digitalTwinPlanningModel";

interface Props {
  goalTitle: string;
  successCriteria: string;
  onDraft: (
    draft: Pick<DigitalWorkerPlan, "title" | "approach" | "steps">,
  ) => void;
}

/** Collect planning intent locally; the existing editor owns explicit saving. */
export default function DigitalTwinPlanning({
  goalTitle,
  successCriteria,
  onDraft,
}: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm<DigitalTwinPlanningIntake>();
  const required = [{ required: true, whitespace: true }];

  function close() {
    setOpen(false);
    form.resetFields();
  }

  return (
    <>
      <Button onClick={() => setOpen(true)}>
        {t("digitalTwinPlanning.open")}
      </Button>
      <Modal
        open={open}
        title={t("digitalTwinPlanning.title")}
        onCancel={close}
        footer={null}
      >
        <Alert
          type="info"
          showIcon
          message={t("digitalTwinPlanning.draftOnly")}
          style={{ marginBottom: 16 }}
        />
        <Typography.Paragraph>
          <strong>{t("digitalTwinPlanning.goal")}:</strong> {goalTitle}
        </Typography.Paragraph>
        <Typography.Paragraph>
          <strong>{t("digitalTwinPlanning.criteria")}:</strong>{" "}
          {successCriteria}
        </Typography.Paragraph>
        <Form
          form={form}
          layout="vertical"
          onFinish={(values) => {
            onDraft(buildDigitalTwinPlanDraft(goalTitle, values, t));
            close();
          }}
        >
          <Form.Item
            name="sources"
            label={t("digitalTwinPlanning.sources")}
            rules={required}
            extra={t("digitalTwinPlanning.sourcesHint")}
          >
            <Input.TextArea maxLength={1000} rows={3} showCount />
          </Form.Item>
          <Form.Item
            name="deliverable"
            label={t("digitalTwinPlanning.deliverable")}
            rules={required}
          >
            <Input.TextArea maxLength={500} rows={2} showCount />
          </Form.Item>
          <Form.Item
            name="reviewer"
            label={t("digitalTwinPlanning.reviewer")}
            rules={required}
          >
            <Input maxLength={120} />
          </Form.Item>
          <Form.Item
            name="cadence"
            label={t("digitalTwinPlanning.cadence")}
            extra={t("digitalTwinPlanning.cadenceHint")}
          >
            <Input maxLength={200} />
          </Form.Item>
          <Button type="primary" htmlType="submit">
            {t("digitalTwinPlanning.continue")}
          </Button>
        </Form>
      </Modal>
    </>
  );
}
