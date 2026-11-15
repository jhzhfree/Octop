import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import RunEvidence from "./RunEvidence";
import {
  organizationsApi,
  type EnterpriseSceneRunEvidence,
} from "../../api/modules/organizations";
vi.mock("../../api/modules/organizations", () => ({
  organizationsApi: { sceneRunEvidence: vi.fn() },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const props = {
  target: { organizationId: "o", runId: "r" },
  currentUserId: 1,
  canRead: true,
  authorizationKey: "manager",
};
const read = () =>
  fireEvent.click(screen.getByText("organizations.viewEvidence"));
beforeEach(() =>
  vi.mocked(organizationsApi.sceneRunEvidence).mockReset().mockResolvedValue({
    run_id: "r",
    available: true,
    answer: "PRIVATE REPORT",
  }),
);
afterEach(cleanup);
it("reads only on click, clears old content on denied reread, and allows explicit retry", async () => {
  render(<RunEvidence {...props} />);
  expect(organizationsApi.sceneRunEvidence).not.toHaveBeenCalled();
  read();
  await screen.findByText("PRIVATE REPORT");
  vi.mocked(organizationsApi.sceneRunEvidence).mockRejectedValueOnce(
    new Error("secret error"),
  );
  read();
  expect(screen.queryByText("PRIVATE REPORT")).toBeNull();
  await screen.findByText("organizations.evidenceLoadFailed");
  expect(screen.queryByText("secret error")).toBeNull();
  read();
  await screen.findByText("PRIVATE REPORT");
});
it.each(["run", "organization", "identity", "permission", "role", "close"])(
  "discards delayed evidence after %s change and a round trip",
  async (change) => {
    let resolve!: (r: EnterpriseSceneRunEvidence) => void;
    vi.mocked(organizationsApi.sceneRunEvidence).mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    const view = render(<RunEvidence {...props} />);
    read();
    read();
    expect(organizationsApi.sceneRunEvidence).toHaveBeenCalledTimes(1);
    if (change === "close")
      fireEvent.click(screen.getByText("runDetails.closeEvidence"));
    else {
      view.rerender(
        <RunEvidence
          {...props}
          target={{
            organizationId: change === "organization" ? "other" : "o",
            runId: change === "run" ? "other" : "r",
          }}
          currentUserId={change === "identity" ? 2 : 1}
          canRead={change !== "permission"}
          authorizationKey={change === "role" ? "member" : "manager"}
        />,
      );
      view.rerender(<RunEvidence {...props} />);
    }
    await act(async () =>
      resolve({ run_id: "r", available: true, answer: "STALE REPORT" }),
    );
    expect(screen.queryByText("STALE REPORT")).toBeNull();
    read();
    await screen.findByText("PRIVATE REPORT");
  },
);
it("distinguishes unavailable evidence from mismatched content", async () => {
  render(<RunEvidence {...props} />);
  vi.mocked(organizationsApi.sceneRunEvidence).mockResolvedValueOnce({
    run_id: "r",
    available: false,
    answer: null,
  });
  read();
  await screen.findByText("organizations.evidenceUnavailable");
  vi.mocked(organizationsApi.sceneRunEvidence).mockResolvedValueOnce({
    run_id: "other",
    available: true,
    answer: "OTHER PRIVATE REPORT",
  });
  read();
  await screen.findByText("organizations.evidenceLoadFailed");
  expect(screen.queryByText("OTHER PRIVATE REPORT")).toBeNull();
});
it("removes displayed content when read permission is revoked", async () => {
  const view = render(<RunEvidence {...props} />);
  read();
  await screen.findByText("PRIVATE REPORT");
  view.rerender(<RunEvidence {...props} canRead={false} />);
  expect(screen.queryByText("PRIVATE REPORT")).toBeNull();
  expect(screen.queryByRole("button")).toBeNull();
});
