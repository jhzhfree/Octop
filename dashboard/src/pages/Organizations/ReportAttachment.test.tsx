import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import ReportAttachment from "./ReportAttachment";
import {
  reportAttachmentApi,
  type ReportPreview,
} from "../../api/modules/reportAttachment";
vi.mock("../../api/modules/reportAttachment", async (load) => ({
  ...(await load<typeof import("../../api/modules/reportAttachment")>()),
  reportAttachmentApi: { get: vi.fn(), preview: vi.fn(), attach: vi.fn() },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ i18n: { language: "en" } }),
}));
const target = { organizationId: "org", runId: "run" };
const preview = {
  report_run_id: "report",
  report_version: 2,
  report_content_hash: "a".repeat(64),
  content: "Inspection: one failed source",
};
const attachment = {
  ...preview,
  run_id: "run",
  organization_id: "org",
  attachment_sha256: "b".repeat(64),
  attached_by: 1,
  attached_at: 1,
};
const props = { target, currentUserId: 1, canAttach: true };
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(reportAttachmentApi.get).mockResolvedValue(null);
  vi.mocked(reportAttachmentApi.preview).mockResolvedValue(preview);
  vi.mocked(reportAttachmentApi.attach).mockResolvedValue(attachment);
});
afterEach(cleanup);
async function load() {
  fireEvent.click(
    screen.getByRole("button", { name: "Link an inspection report" }),
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Report run ID")).not.toBeDisabled(),
  );
  fireEvent.change(screen.getByLabelText("Report run ID"), {
    target: { value: "report" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Preview my report" }));
  await screen.findByText(preview.content);
}
it("loads nothing until explicitly opened and shares only after confirmation", async () => {
  render(<ReportAttachment {...props} />);
  expect(reportAttachmentApi.get).not.toHaveBeenCalled();
  await load();
  expect(reportAttachmentApi.preview).toHaveBeenCalledWith(target, "report");
  expect(reportAttachmentApi.attach).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole("button", { name: "Share and attach this snapshot" }),
  );
  await screen.findByText(
    "Report snapshot attached (not a delivery acceptance)",
  );
  expect(reportAttachmentApi.attach).toHaveBeenCalledExactlyOnceWith(
    target,
    preview,
  );
});
it("cancel preview does not share and existing attachments cannot be replaced", async () => {
  render(<ReportAttachment {...props} />);
  await load();
  fireEvent.click(screen.getByRole("button", { name: "Cancel preview" }));
  expect(reportAttachmentApi.attach).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Close report link" }));
  vi.mocked(reportAttachmentApi.get).mockResolvedValue(attachment);
  fireEvent.click(
    screen.getByRole("button", { name: "Link an inspection report" }),
  );
  await screen.findByText(
    "Report snapshot attached (not a delivery acceptance)",
  );
  expect(
    screen.queryByRole("button", { name: "Share and attach this snapshot" }),
  ).toBeNull();
});
it("does not automatically retry uncertain writes or allow duplicate submission", async () => {
  let reject!: (error: Error) => void;
  vi.mocked(reportAttachmentApi.attach).mockImplementation(
    () =>
      new Promise((_, r) => {
        reject = r;
      }),
  );
  render(<ReportAttachment {...props} />);
  await load();
  const button = screen.getByRole("button", {
    name: "Share and attach this snapshot",
  });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(reportAttachmentApi.attach).toHaveBeenCalledTimes(1);
  await act(async () => reject(new Error("uncertain")));
  expect(
    screen.queryByRole("button", { name: "Share and attach this snapshot" }),
  ).toBeNull();
  expect(screen.getByText(/Unavailable or result uncertain/)).toBeTruthy();
});
it("rejects mismatched previews without revealing their content", async () => {
  vi.mocked(reportAttachmentApi.preview).mockResolvedValue({
    ...preview,
    report_run_id: "other",
    content: "PRIVATE",
  });
  render(<ReportAttachment {...props} />);
  fireEvent.click(
    screen.getByRole("button", { name: "Link an inspection report" }),
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Report run ID")).not.toBeDisabled(),
  );
  fireEvent.change(screen.getByLabelText("Report run ID"), {
    target: { value: "report" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Preview my report" }));
  await screen.findByText(/Unavailable or result uncertain/);
  expect(screen.queryByText("PRIVATE")).toBeNull();
});
it("clears context and ignores late preview after user or organization changes", async () => {
  let resolve!: (value: ReportPreview) => void;
  vi.mocked(reportAttachmentApi.preview).mockImplementation(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  const view = render(<ReportAttachment {...props} />);
  fireEvent.click(
    screen.getByRole("button", { name: "Link an inspection report" }),
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Report run ID")).not.toBeDisabled(),
  );
  fireEvent.change(screen.getByLabelText("Report run ID"), {
    target: { value: "report" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Preview my report" }));
  view.rerender(
    <ReportAttachment
      {...props}
      currentUserId={2}
      target={{ organizationId: "other", runId: "next" }}
    />,
  );
  await act(async () => resolve(preview));
  expect(screen.queryByText(preview.content)).toBeNull();
  expect(
    screen.getByRole("button", { name: "Link an inspection report" }),
  ).toBeTruthy();
});
it("does not expose an entry or read data when unauthorized", () => {
  render(<ReportAttachment {...props} canAttach={false} />);
  expect(screen.queryByRole("button")).toBeNull();
  expect(reportAttachmentApi.get).not.toHaveBeenCalled();
});
