import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import MattermostPublication from "./MattermostPublication";
import {
  mattermostPublicationApi,
  type MattermostPublication as Publication,
} from "../../api/modules/mattermostPublication";
import { mattermostPublicationMessages } from "./mattermostPublicationMessages";
vi.mock("../../api/modules/mattermostPublication", async (load) => ({
  ...(await load<typeof import("../../api/modules/mattermostPublication")>()),
  mattermostPublicationApi: {
    get: vi.fn(),
    candidate: vi.fn(),
    create: vi.fn(),
    decide: vi.fn(),
  },
}));
let language = "en";
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ i18n: { language } }),
}));
const m = mattermostPublicationMessages.en;
const target = { organizationId: "org", runId: "run" };
const record: Publication = {
  organization_id: "org",
  run_id: "run",
  worker_id: "worker",
  worker_version: 2,
  intent_revision: 3,
  configuration_revision: 4,
  instance_key: "company",
  team_id: "t".repeat(26),
  channel_id: "c".repeat(26),
  bot_id: "b".repeat(26),
  report_sha256: "a".repeat(64),
  publication_sha256: "b".repeat(64),
  content:
    "Inspection report <script>unsafe()</script> ![image](https://example.com/track)",
  proposed_by: 1,
  approved_by: null,
  status: "pending_approval",
  created_at: 1,
  updated_at: 1,
  connected: false,
  dispatch_enabled: false,
  execution_authorized: false,
};
const candidate = {
  expected_report_sha256: record.report_sha256,
  expected_worker_version: 2,
  expected_intent_revision: 3,
  expected_configuration_revision: 4,
};
const props = {
  target,
  currentUserId: 2,
  canRead: true,
  canCreate: false,
  canApprove: true,
  canRevoke: true,
};
beforeEach(() => {
  vi.resetAllMocks();
  language = "en";
  vi.mocked(mattermostPublicationApi.get).mockResolvedValue(record);
  vi.mocked(mattermostPublicationApi.candidate).mockResolvedValue(candidate);
  vi.mocked(mattermostPublicationApi.create).mockResolvedValue(record);
  vi.mocked(mattermostPublicationApi.decide).mockResolvedValue({
    ...record,
    status: "blocked_not_connected",
    approved_by: 2,
  });
});
afterEach(cleanup);
async function open() {
  fireEvent.click(screen.getByRole("button", { name: m.open }));
  await screen.findByText(record.content);
}
function approve() {
  fireEvent.click(screen.getByRole("button", { name: m.approve }));
  fireEvent.click(screen.getByRole("button", { name: m.confirm }));
}
it("loads only explicitly; shows exact text and target; requires separate approval confirmation", async () => {
  render(<MattermostPublication {...props} />);
  expect(mattermostPublicationApi.get).not.toHaveBeenCalled();
  await open();
  expect(screen.getByText(m.boundary)).toBeTruthy();
  expect(screen.getByText(`${m.channel}: ${record.channel_id}`)).toBeTruthy();
  expect(document.querySelector("script, img, a")).toBeNull();
  expect(screen.queryByRole("textbox")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: m.approve }));
  expect(mattermostPublicationApi.decide).not.toHaveBeenCalled();
  expect(screen.getByText(m.approveConfirm)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: m.cancel }));
  expect(mattermostPublicationApi.decide).not.toHaveBeenCalled();
  approve();
  await screen.findByText(m.blocked_not_connected);
  expect(mattermostPublicationApi.decide).toHaveBeenCalledExactlyOnceWith(
    target,
    "approve",
    record.publication_sha256,
  );
  expect(screen.queryByRole("button", { name: m.approve })).toBeNull();
});
it("prepares local preview with server candidate only, without automatic approval", async () => {
  vi.mocked(mattermostPublicationApi.get).mockRejectedValue(
    new Error("missing"),
  );
  render(
    <MattermostPublication
      {...props}
      currentUserId={1}
      canCreate
      canApprove={false}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: m.open }));
  await screen.findByText(m.failed);
  expect(mattermostPublicationApi.create).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: m.create }));
  await screen.findByText(record.content);
  expect(mattermostPublicationApi.candidate).toHaveBeenCalledExactlyOnceWith(
    target,
  );
  expect(mattermostPublicationApi.create).toHaveBeenCalledExactlyOnceWith(
    target,
    candidate,
  );
  expect(mattermostPublicationApi.decide).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: m.approve })).toBeNull();
});
it("closing while candidate loads prevents the POST", async () => {
  let resolve!: (value: typeof candidate) => void;
  vi.mocked(mattermostPublicationApi.get).mockRejectedValue(
    new Error("missing"),
  );
  vi.mocked(mattermostPublicationApi.candidate).mockImplementation(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  render(<MattermostPublication {...props} currentUserId={1} canCreate />);
  fireEvent.click(screen.getByRole("button", { name: m.open }));
  await screen.findByText(m.failed);
  fireEvent.click(screen.getByRole("button", { name: m.create }));
  fireEvent.click(screen.getByRole("button", { name: m.close }));
  await act(async () => resolve(candidate));
  expect(mattermostPublicationApi.create).not.toHaveBeenCalled();
  expect(screen.queryByText(record.content)).toBeNull();
});
it("never displays arbitrary API errors or retries an uncertain approval", async () => {
  let reject!: (reason: Error) => void;
  vi.mocked(mattermostPublicationApi.decide).mockImplementation(
    () =>
      new Promise((_, r) => {
        reject = r;
      }),
  );
  render(<MattermostPublication {...props} />);
  await open();
  fireEvent.click(screen.getByRole("button", { name: m.approve }));
  const confirm = screen.getByRole("button", { name: m.confirm });
  fireEvent.click(confirm);
  fireEvent.click(confirm);
  expect(mattermostPublicationApi.decide).toHaveBeenCalledTimes(1);
  await act(async () => reject(new Error("credential secret")));
  expect(screen.getByText(m.failed)).toBeTruthy();
  expect(screen.queryByText(record.content)).toBeNull();
  expect(screen.queryByText("credential secret")).toBeNull();
  expect(screen.queryByRole("button", { name: m.approve })).toBeNull();
});
it("revokes separately, hides report content, and offers no restoration", async () => {
  vi.mocked(mattermostPublicationApi.decide).mockResolvedValue({
    ...record,
    status: "revoked",
    content: "",
  });
  render(<MattermostPublication {...props} />);
  await open();
  fireEvent.click(screen.getByRole("button", { name: m.revoke }));
  expect(mattermostPublicationApi.decide).not.toHaveBeenCalled();
  expect(screen.getByText(m.revokeConfirm)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: m.confirm }));
  await screen.findByText(m.revoked);
  expect(screen.queryByText(record.content)).toBeNull();
  expect(screen.queryByRole("button", { name: m.revoke })).toBeNull();
});
it.each(["revoked", "unknown"] as const)(
  "does not allow decisions in %s",
  async (status) => {
    vi.mocked(mattermostPublicationApi.get).mockResolvedValue({
      ...record,
      status,
    });
    render(<MattermostPublication {...props} />);
    fireEvent.click(screen.getByRole("button", { name: m.open }));
    await screen.findByText(m[status]);
    expect(screen.queryByRole("button", { name: m.approve })).toBeNull();
    expect(screen.queryByRole("button", { name: m.revoke })).toBeNull();
  },
);
it("hides self approval even if a stale caller grants canApprove", async () => {
  render(<MattermostPublication {...props} currentUserId={1} />);
  await open();
  expect(screen.queryByRole("button", { name: m.approve })).toBeNull();
});
it.each([
  { organization_id: "other" },
  { run_id: "other" },
  { connected: true },
  { dispatch_enabled: true },
  { execution_authorized: true },
  { publication_sha256: "bad" },
  { status: "sent" },
  { status: "blocked_not_connected", approved_by: null },
])("rejects invalid responses before displaying content: %j", async (patch) => {
  vi.mocked(mattermostPublicationApi.get).mockResolvedValue({
    ...record,
    ...patch,
  } as Publication);
  render(<MattermostPublication {...props} />);
  fireEvent.click(screen.getByRole("button", { name: m.open }));
  await screen.findByText(m.failed);
  expect(screen.queryByText(record.content)).toBeNull();
});
it.each([
  { target: { organizationId: "other", runId: "run" } },
  { currentUserId: 3 },
  { canRead: false },
  { canApprove: false },
])(
  "drops old responses and pending confirmations on context change: %j",
  async (patch) => {
    let resolve!: (value: Publication) => void;
    vi.mocked(mattermostPublicationApi.get).mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    const view = render(<MattermostPublication {...props} />);
    fireEvent.click(screen.getByRole("button", { name: m.open }));
    view.rerender(<MattermostPublication {...props} {...patch} />);
    await act(async () => resolve(record));
    expect(screen.queryByText(record.content)).toBeNull();
    expect(screen.queryByRole("button", { name: m.confirm })).toBeNull();
  },
);
it("ignores old reads after closing and reopening", async () => {
  let resolve!: (value: Publication) => void;
  vi.mocked(mattermostPublicationApi.get).mockImplementationOnce(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  render(<MattermostPublication {...props} />);
  fireEvent.click(screen.getByRole("button", { name: m.open }));
  fireEvent.click(screen.getByRole("button", { name: m.close }));
  await open();
  await act(async () => resolve({ ...record, content: "OLD CONTENT" }));
  expect(screen.queryByText("OLD CONTENT")).toBeNull();
  expect(screen.getByText(record.content)).toBeTruthy();
});
it("removes a pending approval on permission change", async () => {
  const view = render(<MattermostPublication {...props} />);
  await open();
  fireEvent.click(screen.getByRole("button", { name: m.approve }));
  view.rerender(<MattermostPublication {...props} canApprove={false} />);
  expect(screen.queryByRole("button", { name: m.confirm })).toBeNull();
  expect(screen.queryByText(record.content)).toBeNull();
});
it("rejects a decision response that changes the reviewed destination", async () => {
  vi.mocked(mattermostPublicationApi.decide).mockResolvedValue({
    ...record,
    channel_id: "x".repeat(26),
    status: "blocked_not_connected",
    approved_by: 2,
  });
  render(<MattermostPublication {...props} />);
  await open();
  approve();
  await screen.findByText(m.failed);
  expect(screen.queryByText(m.blocked_not_connected)).toBeNull();
});
it("provides Chinese boundary and action messages", async () => {
  language = "zh-CN";
  render(<MattermostPublication {...props} />);
  fireEvent.click(
    screen.getByRole("button", { name: mattermostPublicationMessages.zh.open }),
  );
  await waitFor(() =>
    expect(
      screen.getByText(mattermostPublicationMessages.zh.boundary),
    ).toBeTruthy(),
  );
  expect(Object.keys(mattermostPublicationMessages.zh).sort()).toEqual(
    Object.keys(m).sort(),
  );
});
