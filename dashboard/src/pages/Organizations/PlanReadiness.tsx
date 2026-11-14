import { Alert, List, Space, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";
import type {
  DigitalWorker,
  DigitalWorkerGoal,
  DigitalWorkerPlan,
  EnterpriseScene,
  EnterpriseSceneRun,
} from "../../api/modules/organizations";
import { inspectPlanReadiness, selectPlanRuns } from "./planReadiness";

interface Props {
  worker: DigitalWorker;
  goal: DigitalWorkerGoal;
  plan: DigitalWorkerPlan;
  scenes: EnterpriseScene[];
  runs: EnterpriseSceneRun[];
}

export default function PlanReadiness({
  worker,
  goal,
  plan,
  scenes,
  runs,
}: Props) {
  const { t } = useTranslation();
  const checks = inspectPlanReadiness(worker, goal, plan, scenes);
  const history = selectPlanRuns(worker, goal, plan, runs);
  return (
    <section aria-label={t("planReadiness.title")}>
      <Typography.Title level={5}>{t("planReadiness.title")}</Typography.Title>
      <Alert type="info" showIcon message={t("planReadiness.boundary")} />
      <List
        size="small"
        dataSource={Object.entries(checks)}
        renderItem={([key, observed]) => (
          <List.Item>
            <Space>
              <Tag color={observed ? "blue" : "orange"}>
                {t(
                  observed ? "planReadiness.observed" : "planReadiness.missing",
                )}
              </Tag>
              {t(`planReadiness.checks.${key}`)}
            </Space>
          </List.Item>
        )}
      />
      <Typography.Title level={5}>{t("planReadiness.runs")}</Typography.Title>
      <Typography.Paragraph type="secondary">
        {t("planReadiness.runBoundary")}
      </Typography.Paragraph>
      <List
        size="small"
        dataSource={history}
        pagination={history.length > 5 ? { pageSize: 5 } : false}
        locale={{ emptyText: t("planReadiness.noRuns") }}
        renderItem={(run) => (
          <List.Item key={run.run_id}>
            <Space wrap>
              <Typography.Text code>{run.run_id}</Typography.Text>
              <Tag>{t(`organizations.runStatuses.${run.status}`)}</Tag>
              <Tag>
                {!run.plan_snapshot
                  ? t("planReadiness.unknownVersion")
                  : t(
                      run.plan_snapshot.plan.version === plan.version
                        ? "planReadiness.currentVersion"
                        : "planReadiness.otherVersion",
                      { version: run.plan_snapshot.plan.version },
                    )}
              </Tag>
            </Space>
          </List.Item>
        )}
      />
    </section>
  );
}
