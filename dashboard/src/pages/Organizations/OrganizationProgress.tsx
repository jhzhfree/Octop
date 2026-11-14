import { useEffect, useRef, useState } from "react";
import { Alert, Button, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";
import {
  deliveryKeys,
  executionKeys,
  organizationProgressApi,
  validProgress,
  type OrganizationProgress as Summary,
} from "../../api/modules/organizationProgress";

/** Local visibility is not authorization; the server rechecks organization membership. */
export default function OrganizationProgress({
  organizationId,
  canManage,
}: {
  organizationId: string;
  canManage: boolean;
}) {
  const { i18n } = useTranslation();
  const zh = (i18n.resolvedLanguage || i18n.language || "en").startsWith("zh");
  const key = `${organizationId}:${canManage}`;
  const generation = useRef(0);
  const busy = useRef(false);
  const [days, setDays] = useState(30);
  const [state, setState] = useState<{
    key: string;
    loading?: boolean;
    error?: boolean;
    data?: Summary;
  }>({ key });
  useEffect(() => {
    generation.current++;
    busy.current = false;
    return () => {
      // Invalidate all outstanding requests, not a captured DOM ref.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      generation.current++;
    };
  }, [key]);
  const visible = state.key === key ? state : undefined;
  const load = async () => {
    if (!canManage || busy.current) return;
    busy.current = true;
    const version = ++generation.current;
    const end = Math.floor(Date.now() / 1000),
      start = end - days * 86400;
    setState({ key, loading: true });
    try {
      const data = await organizationProgressApi.read(
        organizationId,
        start,
        end,
      );
      if (!validProgress(data, organizationId, start, end))
        throw new Error("Invalid summary");
      if (version === generation.current) setState({ key, data });
    } catch {
      if (version === generation.current) setState({ key, error: true });
    } finally {
      if (version === generation.current) busy.current = false;
    }
  };
  const data = visible?.data;
  return (
    <Space direction="vertical">
      <Typography.Title level={5}>
        {zh ? "组织运行进展" : "Organization run progress"}
      </Typography.Title>
      <Alert
        type="info"
        message={
          zh
            ? "仅持久化运行记录，状态可能滞后；未核验外部系统。执行成功不等于人工验收或目标完成。"
            : "Persisted runs only; state may lag. External systems are unverified. Execution success is not acceptance or goal completion."
        }
      />
      {!canManage ? (
        <Alert
          type="warning"
          message={
            zh
              ? "需要组织管理权限，未读取数据。"
              : "Organization manager permission required; no data requested."
          }
        />
      ) : (
        <>
          <label>
            {zh ? "创建时间窗口" : "Creation-time window"}{" "}
            <select
              aria-label="Window days"
              value={days}
              disabled={visible?.loading}
              onChange={(e) => {
                setDays(Number(e.target.value));
                setState({ key });
              }}
            >
              {[1, 7, 30, 90].map((n) => (
                <option key={n} value={n}>
                  {n} {zh ? "天" : "days"}
                </option>
              ))}
            </select>
          </label>
          <Button loading={visible?.loading} onClick={() => void load()}>
            {zh ? "读取进展" : "Load progress"}
          </Button>
          {visible?.error && (
            <Alert
              type="error"
              message={
                zh
                  ? "无法读取：可能无权限或服务不可用。没有可展示的统计。"
                  : "Unavailable: access may be denied or the service unavailable. No counts to display."
              }
            />
          )}
          {data && (
            <>
              <Typography.Text>{`as_of: ${new Date(
                data.as_of * 1000,
              ).toISOString()}`}</Typography.Text>
              <Typography.Text>{`[${new Date(
                data.window_start * 1000,
              ).toISOString()}, ${new Date(
                data.window_end_exclusive * 1000,
              ).toISOString()}) UTC`}</Typography.Text>
              {data.limit_exceeded || !data.counts ? (
                <Alert
                  type="warning"
                  message={
                    zh
                      ? `超过 ${data.max_runs} 条上限，请缩短窗口；不展示部分统计。`
                      : `Limit of ${data.max_runs} runs exceeded. Shorten the window; partial counts are withheld.`
                  }
                />
              ) : (
                <>
                  <Typography.Text>
                    {zh
                      ? `运行记录数：${data.denominator}`
                      : `Run records: ${data.denominator}`}
                  </Typography.Text>
                  <Typography.Text>
                    {zh
                      ? `异常/未知记录：${data.inconsistent_records}`
                      : `Inconsistent/unknown records: ${data.inconsistent_records}`}
                  </Typography.Text>
                  <ul>
                    {executionKeys.map((k) => (
                      <li key={k}>
                        {k}: {data.counts!.execution[k]}
                      </li>
                    ))}
                  </ul>
                  <ul>
                    {deliveryKeys.map((k) => (
                      <li key={k}>
                        {k}: {data.counts!.delivery[k]}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </>
          )}
        </>
      )}
    </Space>
  );
}
