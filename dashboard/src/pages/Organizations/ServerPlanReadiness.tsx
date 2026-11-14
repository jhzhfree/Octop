import { useEffect, useRef, useState } from "react";
import { Alert, Button, List, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";
import {
  inspectServerReadiness,
  matchesReadinessTarget,
  type ReadinessTarget,
  type ServerReadinessReport,
} from "../../api/modules/planReadiness";
import { serverReadinessMessages } from "./serverReadinessMessages";

export default function ServerPlanReadiness({
  target,
}: {
  target: ReadinessTarget;
}) {
  const { t, i18n } = useTranslation();
  const messages =
    serverReadinessMessages[
      (i18n.resolvedLanguage || i18n.language || "en").startsWith("zh")
        ? "zh"
        : "en"
    ];
  const label = (key: string) =>
    t(`serverPlanReadiness.${key}`, {
      defaultValue: messages[key as keyof typeof messages] || key,
    });
  const key = JSON.stringify(target);
  const generation = useRef(0);
  const [state, setState] = useState<{
    key: string;
    loading?: boolean;
    failed?: boolean;
    report?: ServerReadinessReport;
  }>({ key });
  useEffect(() => {
    generation.current += 1;
    return () => {
      generation.current += 1;
    };
  }, [key]);
  const inspect = async () => {
    const seq = ++generation.current;
    setState({ key, loading: true });
    try {
      const report = await inspectServerReadiness(target);
      if (!matchesReadinessTarget(report, target))
        throw new Error("stale response");
      if (generation.current === seq) setState({ key, report });
    } catch {
      if (generation.current === seq) setState({ key, failed: true });
    }
  };
  const current = state.key === key ? state : undefined;
  return (
    <section aria-label={label("title")}>
      <Typography.Title level={5}>{label("title")}</Typography.Title>
      <Alert type="warning" showIcon message={label("boundary")} />
      <Button loading={current?.loading} onClick={() => void inspect()}>
        {label("inspect")}
      </Button>
      {current?.failed && <Alert type="error" message={label("failed")} />}
      {current?.report && (
        <>
          <Typography.Text type="secondary">
            {current.report.observed_at}
          </Typography.Text>
          <List
            dataSource={current.report.checks}
            renderItem={(check) => (
              <List.Item>
                <Tag
                  color={
                    check.status === "satisfied"
                      ? "blue"
                      : check.status === "unmet"
                      ? "orange"
                      : "default"
                  }
                >
                  {label(check.status)}
                </Tag>
                {label(check.code)}
              </List.Item>
            )}
          />
        </>
      )}
    </section>
  );
}
