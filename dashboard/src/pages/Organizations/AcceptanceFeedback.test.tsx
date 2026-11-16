import { SensitiveAccessContext } from "../../components/SensitiveAccessBoundary";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import AcceptanceFeedback from "./AcceptanceFeedback";
import {
  deliveryAcceptanceApi,
  type AcceptanceRecord,
} from "../../api/modules/deliveryAcceptance";
vi.mock("../../api/modules/deliveryAcceptance", async (original) => ({
  ...(await original<typeof import("../../api/modules/deliveryAcceptance")>()),
  deliveryAcceptanceApi: { feedback: vi.fn() },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ i18n: { language: "en" } }),
}));
const target = { organizationId: "o", runId: "r" };
const record: AcceptanceRecord = {
  run_id: "r",
  organization_id: "o",
  decision: "changes_requested",
  reviewer_id: 2,
  reviewed_at: 1,
  evidence_sha256: "a".repeat(64),
  snapshot_sha256: "b".repeat(64),
  evidence_length: 1,
  scene_version: 1,
  review_note: "Add retry evidence <script>bad</script>",
};
beforeEach(() =>
  vi
    .mocked(deliveryAcceptanceApi.feedback)
    .mockReset()
    .mockResolvedValue(record),
);
it("requester explicitly reads feedback as plain text", async () => {
  const view = render(<AcceptanceFeedback target={target} currentUserId={1} />);
  expect(deliveryAcceptanceApi.feedback).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole("button", { name: "Read acceptance feedback" }),
  );
  expect(await screen.findByText(record.review_note!)).toBeInTheDocument();
  expect(view.container.querySelector("script")).toBeNull();
});
it("clears feedback when identity or run changes and ignores late responses", async () => {
  let resolve!: (value: AcceptanceRecord) => void;
  vi.mocked(deliveryAcceptanceApi.feedback).mockReturnValueOnce(
    new Promise((r) => {
      resolve = r;
    }),
  );
  const view = render(<AcceptanceFeedback target={target} currentUserId={1} />);
  fireEvent.click(
    screen.getByRole("button", { name: "Read acceptance feedback" }),
  );
  view.rerender(
    <AcceptanceFeedback
      target={{ ...target, runId: "other" }}
      currentUserId={3}
    />,
  );
  await act(async () => resolve(record));
  expect(screen.queryByText(record.review_note!)).toBeNull();
});
it("does not show feedback belonging to a different run", async () => {
  vi.mocked(deliveryAcceptanceApi.feedback).mockResolvedValue({
    ...record,
    run_id: "other",
  });
  render(<AcceptanceFeedback target={target} currentUserId={1} />);
  fireEvent.click(
    screen.getByRole("button", { name: "Read acceptance feedback" }),
  );
  await screen.findByRole("alert");
  expect(screen.queryByText(record.review_note!)).toBeNull();
});
it("shows a clear empty state for pending acceptance", async () => {
  vi.mocked(deliveryAcceptanceApi.feedback).mockResolvedValue(null);
  render(<AcceptanceFeedback target={target} currentUserId={1} />);
  fireEvent.click(
    screen.getByRole("button", { name: "Read acceptance feedback" }),
  );
  expect(
    await screen.findByText("No acceptance decision yet."),
  ).toBeInTheDocument();
});

it("destroys visible feedback when sensitive access becomes unavailable", async () => {
  const view = render(
    <SensitiveAccessContext.Provider value={true}>
      <AcceptanceFeedback target={target} currentUserId={1} />
    </SensitiveAccessContext.Provider>,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Read acceptance feedback" }),
  );
  await screen.findByText(record.review_note!);
  view.rerender(
    <SensitiveAccessContext.Provider value={false}>
      <AcceptanceFeedback target={target} currentUserId={1} />
    </SensitiveAccessContext.Provider>,
  );
  expect(screen.queryByText(record.review_note!)).toBeNull();
});
