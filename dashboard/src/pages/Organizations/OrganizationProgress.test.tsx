import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import OrganizationProgress from "./OrganizationProgress";
import {
  organizationProgressApi,
  type OrganizationProgress as Summary,
} from "../../api/modules/organizationProgress";
vi.mock("../../api/modules/organizationProgress", async (original) => ({
  ...(await original<
    typeof import("../../api/modules/organizationProgress")
  >()),
  organizationProgressApi: { read: vi.fn() },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ i18n: { language: "en" } }),
}));
function result(org: string, start: number, end: number): Summary {
  return {
    organization_id: org,
    window_start: start,
    window_end_exclusive: end,
    as_of: end,
    scope: "persisted_runs_by_creation_time",
    unit: "run",
    max_runs: 10000,
    limit_exceeded: false,
    denominator: 0,
    counts: {
      execution: {
        awaiting_approval: 0,
        rejected: 0,
        queued: 0,
        succeeded: 0,
        failed: 0,
      },
      delivery: { unreviewed_success: 0, accepted: 0, changes_requested: 0 },
    },
    inconsistent_records: 0,
    persisted_state_may_lag: true,
    external_systems_verified: false,
    execution_authorized: false,
  };
}
beforeEach(() => {
  vi.mocked(organizationProgressApi.read)
    .mockReset()
    .mockImplementation(async (org, start, end) => result(org, start, end));
});
afterEach(cleanup);
const load = () =>
  fireEvent.click(screen.getByRole("button", { name: "Load progress" }));
it("does not fetch without manager access or show fake zeros", () => {
  render(<OrganizationProgress organizationId="o" canManage={false} />);
  expect(organizationProgressApi.read).not.toHaveBeenCalled();
  expect(screen.queryByText("Run records: 0")).toBeNull();
});
it("shows genuine empty data, as_of and explicit lag", async () => {
  render(<OrganizationProgress organizationId="o" canManage />);
  load();
  expect(await screen.findByText("Run records: 0")).toBeTruthy();
  expect(screen.getByText(/as_of:/)).toBeTruthy();
  expect(screen.getByText(/state may lag/)).toBeTruthy();
});
it("withholds partial counts on cap", async () => {
  vi.mocked(organizationProgressApi.read).mockImplementation(
    async (...args) => ({
      ...result(...args),
      limit_exceeded: true,
      denominator: null,
      counts: null,
    }),
  );
  render(<OrganizationProgress organizationId="o" canManage />);
  load();
  expect(await screen.findByText(/partial counts are withheld/)).toBeTruthy();
  expect(screen.queryByText(/Run records:/)).toBeNull();
});
it("rejects mismatch and allows retry", async () => {
  vi.mocked(organizationProgressApi.read).mockImplementationOnce(
    async (_, start, end) => result("other", start, end),
  );
  render(<OrganizationProgress organizationId="o" canManage />);
  load();
  expect(await screen.findByText(/No counts to display/)).toBeTruthy();
  load();
  expect(await screen.findByText("Run records: 0")).toBeTruthy();
});
it("suppresses old organization response", async () => {
  let resolve!: (r: Summary) => void;
  let args!: [string, number, number];
  vi.mocked(organizationProgressApi.read).mockImplementation((...a) => {
    args = a;
    return new Promise((r) => {
      resolve = r;
    });
  });
  const view = render(<OrganizationProgress organizationId="o" canManage />);
  load();
  view.rerender(<OrganizationProgress organizationId="other" canManage />);
  resolve(result(...args));
  await waitFor(() => expect(screen.queryByText(/Run records:/)).toBeNull());
});
it("clears previous counts when window changes", async () => {
  render(<OrganizationProgress organizationId="o" canManage />);
  load();
  await screen.findByText("Run records: 0");
  fireEvent.change(screen.getByLabelText("Window days"), {
    target: { value: "7" },
  });
  expect(screen.queryByText(/Run records:/)).toBeNull();
});

it.each([true, undefined, null, "false"])(
  "rejects invalid execution authorization marker %s",
  async (marker) => {
    vi.mocked(organizationProgressApi.read).mockImplementation(
      async (...args) =>
        ({
          ...result(...args),
          execution_authorized: marker,
        }) as unknown as Summary,
    );
    render(<OrganizationProgress organizationId="o" canManage />);
    load();
    expect(await screen.findByText(/No counts to display/)).toBeTruthy();
    expect(screen.queryByText(/Run records:/)).toBeNull();
  },
);
