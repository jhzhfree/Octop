import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import AccessSession, { ACCESS_INTERVAL_MS } from "./AccessSession";
import RunEvidence from "../pages/Organizations/RunEvidence";
import RunOperationTrace from "../pages/Organizations/RunOperationTrace";
import { authApi, type AccessState } from "../api/modules/auth";
import {
  organizationsApi,
  type EnterpriseSceneRunEvidence,
} from "../api/modules/organizations";
import { operationTraceApi } from "../api/modules/operationTrace";
vi.mock("../api/modules/auth", async (original) => ({
  ...(await original<typeof import("../api/modules/auth")>()),
  authApi: { accessState: vi.fn() },
}));
vi.mock("../api/modules/organizations", () => ({
  organizationsApi: { sceneRunEvidence: vi.fn() },
}));
vi.mock("../api/modules/operationTrace", async (original) => ({
  ...(await original<typeof import("../api/modules/operationTrace")>()),
  operationTraceApi: { read: vi.fn() },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));
const good: AccessState = {
  revision: "a".repeat(64),
  user: {
    id: 1,
    role: "user",
    username: "fixture",
    display_name: null,
    locale: "en",
    permissions: ["tasks"],
  },
};
function App() {
  return (
    <AccessSession user={good.user} setUser={vi.fn()}>
      <RunEvidence
        target={{ organizationId: "o", runId: "r" }}
        currentUserId={1}
        canRead
      />
      <RunOperationTrace organizationId="other" runId="s" currentUserId={1} />
    </AccessSession>
  );
}
const flush = async () => {
  await act(async () => {});
};
beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
  vi.mocked(authApi.accessState).mockReset().mockResolvedValue(good);
  vi.mocked(organizationsApi.sceneRunEvidence).mockReset().mockResolvedValue({
    run_id: "r",
    available: true,
    answer: "PRIVATE REPORT",
  });
  vi.mocked(operationTraceApi.read)
    .mockReset()
    .mockResolvedValue({
      organization_id: "other",
      run_id: "s",
      as_of: 1,
      history_complete: false,
      execution_authorized: false,
      truncated: false,
      execution_context: {
        agent_id: "a",
        on_behalf_of_user_id: 1,
        bound_cron_id: null,
        configured_executor: "unknown",
        schedule_kind: "unknown",
      },
      events: [],
    });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});
it("clears real report and trace surfaces across organizations after access change", async () => {
  render(<App />);
  await flush();
  fireEvent.click(screen.getByText("organizations.viewEvidence"));
  fireEvent.click(screen.getByText("operationTrace.view"));
  await flush();
  expect(screen.getByText("PRIVATE REPORT")).toBeTruthy();
  expect(
    screen.getByRole("region", { name: "operationTrace.title" }),
  ).toBeTruthy();
  vi.mocked(authApi.accessState).mockResolvedValue({
    ...good,
    revision: "b".repeat(64),
    user: { ...good.user, permissions: [] },
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ACCESS_INTERVAL_MS);
  });
  expect(screen.queryByText("PRIVATE REPORT")).toBeNull();
  expect(
    screen.queryByRole("region", { name: "operationTrace.title" }),
  ).toBeNull();
  expect(organizationsApi.sceneRunEvidence).toHaveBeenCalledTimes(1);
  expect(operationTraceApi.read).toHaveBeenCalledTimes(1);
});
it("unmounts pending real evidence on visibility suspension and discards its late result", async () => {
  let resolve!: (value: EnterpriseSceneRunEvidence) => void;
  vi.mocked(organizationsApi.sceneRunEvidence).mockImplementationOnce(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  render(<App />);
  await flush();
  fireEvent.click(screen.getByText("organizations.viewEvidence"));
  vi.spyOn(document, "hidden", "get").mockReturnValue(true);
  fireEvent(document, new Event("visibilitychange"));
  await act(async () =>
    resolve({ run_id: "r", available: true, answer: "LATE PRIVATE REPORT" }),
  );
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  fireEvent(document, new Event("visibilitychange"));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(screen.queryByText("LATE PRIVATE REPORT")).toBeNull();
  expect(organizationsApi.sceneRunEvidence).toHaveBeenCalledTimes(1);
});
