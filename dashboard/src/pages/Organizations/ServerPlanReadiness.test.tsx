import {
  fireEvent,
  render,
  screen,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ServerPlanReadiness from "./ServerPlanReadiness";
import {
  inspectServerReadiness,
  type ServerReadinessReport,
} from "../../api/modules/planReadiness";
vi.mock("../../api/modules/planReadiness", async (original) => ({
  ...(await original<typeof import("../../api/modules/planReadiness")>()),
  inspectServerReadiness: vi.fn(),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (_key: string, options: { defaultValue: string }) =>
      options.defaultValue,
    i18n: { language: "en" },
  }),
}));
const target = {
  organizationId: "o",
  workerId: "w",
  goalId: "g",
  planId: "p",
  workerVersion: 1,
  planVersion: 2,
};
const report: ServerReadinessReport = {
  organization_id: "o",
  worker_id: "w",
  goal_id: "g",
  plan_id: "p",
  worker_version: 1,
  capability_version: 1,
  plan_version: 2,
  goal_updated_at: 0,
  observed_at: "2026-11-14T00:00:00Z",
  execution_authorized: false,
  scope: "local_observation_not_atomic",
  checks: [{ code: "knowledge_user_acl", status: "unknown" }],
};
afterEach(cleanup);
beforeEach(() => vi.mocked(inspectServerReadiness).mockReset());
describe("server read-only observations", () => {
  it("keeps unknown distinct and never offers execution", async () => {
    vi.mocked(inspectServerReadiness).mockResolvedValue(report);
    render(<ServerPlanReadiness target={target} />);
    expect(inspectServerReadiness).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button"));
    expect(await screen.findByText("Unknown / unverified")).toBeTruthy();
    expect(screen.getAllByRole("button")).toHaveLength(1);
  });
  it.each(["403 forbidden", "409 version conflict"])(
    "clears old observations on %s",
    async (error) => {
      vi.mocked(inspectServerReadiness)
        .mockResolvedValueOnce(report)
        .mockRejectedValueOnce(new Error(error));
      render(<ServerPlanReadiness target={target} />);
      fireEvent.click(screen.getByRole("button"));
      await screen.findByText("Unknown / unverified");
      fireEvent.click(screen.getByRole("button"));
      await screen.findByText(/Observation unavailable/);
      expect(screen.queryByText(report.observed_at)).toBeNull();
    },
  );
  it("rejects mismatched version responses", async () => {
    vi.mocked(inspectServerReadiness).mockResolvedValue({
      ...report,
      plan_version: 3,
    });
    render(<ServerPlanReadiness target={target} />);
    fireEvent.click(screen.getByRole("button"));
    await screen.findByText(/Observation unavailable/);
    expect(screen.queryByText(report.observed_at)).toBeNull();
  });
  it("ignores delayed response after selection changes", async () => {
    let resolve!: (value: ServerReadinessReport) => void;
    vi.mocked(inspectServerReadiness).mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    const { rerender } = render(<ServerPlanReadiness target={target} />);
    fireEvent.click(screen.getByRole("button"));
    rerender(<ServerPlanReadiness target={{ ...target, planId: "new" }} />);
    resolve(report);
    await waitFor(() =>
      expect(screen.queryByText(report.observed_at)).toBeNull(),
    );
  });
});
