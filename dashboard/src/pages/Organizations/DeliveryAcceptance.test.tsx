import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DeliveryAcceptance from "./DeliveryAcceptance";
import {
  deliveryAcceptanceApi,
  type AcceptanceReview,
  type AcceptanceRecord,
} from "../../api/modules/deliveryAcceptance";
vi.mock("../../api/modules/deliveryAcceptance", async (original) => ({
  ...(await original<typeof import("../../api/modules/deliveryAcceptance")>()),
  deliveryAcceptanceApi: { review: vi.fn(), decide: vi.fn() },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ i18n: { language: "en" } }),
}));
const target = { organizationId: "o", runId: "r" };
const review: AcceptanceReview = {
  run_id: "r",
  answer: "Visible summary <script>bad</script>",
  evidence_sha256: "a".repeat(64),
  snapshot_sha256: "b".repeat(64),
  evidence_length: 20,
  acceptance: null,
};
const record: AcceptanceRecord = {
  run_id: "r",
  organization_id: "o",
  decision: "accepted",
  reviewer_id: 42,
  reviewed_at: 1700000000,
  evidence_sha256: review.evidence_sha256,
  snapshot_sha256: review.snapshot_sha256,
  evidence_length: 20,
  scene_version: 1,
};
beforeEach(() => {
  vi.mocked(deliveryAcceptanceApi.review).mockReset().mockResolvedValue(review);
  vi.mocked(deliveryAcceptanceApi.decide).mockReset().mockResolvedValue(record);
});
afterEach(cleanup);
const open = async () => {
  fireEvent.click(
    screen.getByRole("button", { name: "Review delivery summary" }),
  );
  await screen.findByText(review.answer);
};
describe("delivery acceptance", () => {
  it("does not request or expose entry for unauthorized viewers", () => {
    render(
      <DeliveryAcceptance
        currentUserId={42}
        target={target}
        canReview={false}
      />,
    );
    expect(screen.queryByRole("button")).toBeNull();
    expect(deliveryAcceptanceApi.review).not.toHaveBeenCalled();
  });
  it("clears visible evidence when local permission is revoked", async () => {
    const { rerender } = render(
      <DeliveryAcceptance currentUserId={42} target={target} canReview />,
    );
    await open();
    rerender(
      <DeliveryAcceptance
        currentUserId={42}
        target={target}
        canReview={false}
      />,
    );
    expect(screen.queryByText(review.answer)).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    rerender(
      <DeliveryAcceptance currentUserId={42} target={target} canReview />,
    );
    expect(screen.queryByText(review.answer)).toBeNull();
    expect(screen.queryByRole("group")).toBeNull();
    expect(deliveryAcceptanceApi.review).toHaveBeenCalledTimes(1);
    await open();
    expect(deliveryAcceptanceApi.review).toHaveBeenCalledTimes(2);
  });
  it.each(["run", "organization", "identity"])(
    "destroys evidence and pending choice across a %s round trip",
    async (context) => {
      const { rerender } = render(
        <DeliveryAcceptance currentUserId={42} target={target} canReview />,
      );
      await open();
      fireEvent.click(screen.getByRole("button", { name: "Accept summary" }));
      const nextTarget =
        context === "run"
          ? { ...target, runId: "other" }
          : context === "organization"
          ? { ...target, organizationId: "other" }
          : target;
      rerender(
        <DeliveryAcceptance
          currentUserId={context === "identity" ? 43 : 42}
          target={nextTarget}
          canReview
        />,
      );
      rerender(
        <DeliveryAcceptance currentUserId={42} target={target} canReview />,
      );
      expect(screen.queryByText(review.answer)).toBeNull();
      expect(screen.queryByRole("group")).toBeNull();
      expect(deliveryAcceptanceApi.review).toHaveBeenCalledTimes(1);
      expect(deliveryAcceptanceApi.decide).not.toHaveBeenCalled();
      await open();
      expect(deliveryAcceptanceApi.review).toHaveBeenCalledTimes(2);
      expect(screen.queryByRole("group")).toBeNull();
    },
  );
  it("reads an existing decision without allowing it to be overwritten", async () => {
    vi.mocked(deliveryAcceptanceApi.review).mockResolvedValue({
      ...review,
      acceptance: record,
    });
    render(<DeliveryAcceptance currentUserId={42} target={target} canReview />);
    await open();
    expect(screen.getByText("Accepted by a human")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Accept summary" })).toBeNull();
    expect(deliveryAcceptanceApi.decide).not.toHaveBeenCalled();
  });
  it("requires explicit read, decision and confirmation and binds hashes", async () => {
    const { container } = render(
      <DeliveryAcceptance currentUserId={42} target={target} canReview />,
    );
    expect(deliveryAcceptanceApi.review).not.toHaveBeenCalled();
    await open();
    expect(container.querySelector("script")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Accept summary" }));
    expect(deliveryAcceptanceApi.decide).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm permanent decision" }),
    );
    await screen.findByText("Accepted by a human");
    expect(deliveryAcceptanceApi.decide).toHaveBeenCalledExactlyOnceWith(
      target,
      "accepted",
      review,
    );
    expect(screen.queryByRole("button", { name: "Accept summary" })).toBeNull();
  });
  it("cancel confirmation and close do not write", async () => {
    render(<DeliveryAcceptance currentUserId={42} target={target} canReview />);
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Request changes" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel decision" }));
    fireEvent.click(screen.getByRole("button", { name: "Close review" }));
    expect(deliveryAcceptanceApi.decide).not.toHaveBeenCalled();
    expect(screen.queryByText(review.answer)).toBeNull();
  });
  it.each(["403 revoked", "409 stale", "network uncertain"])(
    "clears evidence on %s without retry",
    async (error) => {
      vi.mocked(deliveryAcceptanceApi.decide).mockRejectedValue(
        new Error(error),
      );
      render(
        <DeliveryAcceptance currentUserId={42} target={target} canReview />,
      );
      await open();
      fireEvent.click(screen.getByRole("button", { name: "Accept summary" }));
      fireEvent.click(
        screen.getByRole("button", { name: "Confirm permanent decision" }),
      );
      await screen.findByText(/Review unavailable/);
      expect(screen.queryByText(review.answer)).toBeNull();
      expect(screen.queryByText("Accepted by a human")).toBeNull();
      expect(deliveryAcceptanceApi.decide).toHaveBeenCalledTimes(1);
    },
  );
  it("ignores a delayed review after target change", async () => {
    let resolve!: (x: AcceptanceReview) => void;
    vi.mocked(deliveryAcceptanceApi.review).mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    const { rerender } = render(
      <DeliveryAcceptance currentUserId={42} target={target} canReview />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Review delivery summary" }),
    );
    rerender(
      <DeliveryAcceptance
        currentUserId={42}
        target={{ ...target, runId: "other" }}
        canReview
      />,
    );
    resolve(review);
    await waitFor(() => expect(screen.queryByText(review.answer)).toBeNull());
  });
  it("does not duplicate pending submission and ignores result after closing", async () => {
    let resolve!: (x: AcceptanceRecord) => void;
    vi.mocked(deliveryAcceptanceApi.decide).mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    render(<DeliveryAcceptance currentUserId={42} target={target} canReview />);
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Accept summary" }));
    const confirm = screen.getByRole("button", {
      name: "Confirm permanent decision",
    });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(deliveryAcceptanceApi.decide).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Close review" }));
    resolve(record);
    await waitFor(() =>
      expect(screen.queryByText("Accepted by a human")).toBeNull(),
    );
  });
  it("discards a pending decision after an identity round trip", async () => {
    let resolve!: (x: AcceptanceRecord) => void;
    vi.mocked(deliveryAcceptanceApi.decide).mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    const { rerender } = render(
      <DeliveryAcceptance currentUserId={42} target={target} canReview />,
    );
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Accept summary" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm permanent decision" }),
    );
    rerender(
      <DeliveryAcceptance currentUserId={43} target={target} canReview />,
    );
    rerender(
      <DeliveryAcceptance currentUserId={42} target={target} canReview />,
    );
    resolve(record);
    await waitFor(() =>
      expect(screen.queryByText("Accepted by a human")).toBeNull(),
    );
    expect(screen.queryByText(review.answer)).toBeNull();
    expect(screen.queryByRole("group")).toBeNull();
    expect(deliveryAcceptanceApi.decide).toHaveBeenCalledTimes(1);
  });
  it("rejects mismatched success response", async () => {
    vi.mocked(deliveryAcceptanceApi.decide).mockResolvedValue({
      ...record,
      run_id: "other",
    });
    render(<DeliveryAcceptance currentUserId={42} target={target} canReview />);
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Accept summary" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm permanent decision" }),
    );
    await screen.findByText(/Review unavailable/);
    expect(screen.queryByText("Accepted by a human")).toBeNull();
  });
});

it("labels verified automatic reports and human-shared snapshots separately before a manual decision", async () => {
  vi.mocked(deliveryAcceptanceApi.review).mockResolvedValue({
    ...review,
    evidence_sources: [
      {
        kind: "same_run_http_report",
        source_run_id: "r",
        content_sha256: "c".repeat(64),
      },
      {
        kind: "owner_shared_report",
        source_run_id: "historical-report",
        content_sha256: "d".repeat(64),
      },
    ],
  });
  const view = render(
    <DeliveryAcceptance target={target} currentUserId={42} canReview />,
  );
  await open();
  expect(screen.getByText(/Automatic local report/)).toBeTruthy();
  expect(screen.getByText(/Manually shared report snapshot/)).toBeTruthy();
  expect(screen.getByText(/historical-report/)).toBeTruthy();
  expect(screen.getByText(/c{64}/)).toBeTruthy();
  expect(deliveryAcceptanceApi.decide).not.toHaveBeenCalled();
  view.rerender(
    <DeliveryAcceptance target={target} currentUserId={42} canReview={false} />,
  );
  expect(screen.queryByText(/historical-report/)).toBeNull();
});
it("shows unavailable source metadata explicitly for older servers", async () => {
  render(<DeliveryAcceptance target={target} currentUserId={42} canReview />);
  await open();
  expect(screen.getByText(/Source metadata unavailable/)).toBeTruthy();
  expect(screen.queryByText(/Automatic local report/)).toBeNull();
});
it("rejects source metadata from another run without showing its content", async () => {
  vi.mocked(deliveryAcceptanceApi.review).mockResolvedValue({
    ...review,
    evidence_sources: [
      {
        kind: "same_run_http_report",
        source_run_id: "other",
        content_sha256: review.evidence_sha256,
      },
    ],
  });
  render(<DeliveryAcceptance target={target} currentUserId={42} canReview />);
  fireEvent.click(
    screen.getByRole("button", { name: "Review delivery summary" }),
  );
  await screen.findByText(/Review unavailable/);
  expect(screen.queryByText(review.answer)).toBeNull();
  expect(screen.queryByText(/Automatic local report/)).toBeNull();
});
