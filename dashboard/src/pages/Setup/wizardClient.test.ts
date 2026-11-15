import { beforeEach, expect, it, vi } from "vitest";
import { request } from "../../api/request";
import {
  resolveSetupProbeToken,
  wizardApi,
  wizardSession,
} from "./wizardClient";
vi.mock("../../api/request", () => ({ request: vi.fn() }));
beforeEach(() => {
  vi.resetAllMocks();
  sessionStorage.clear();
});
it("never resumes setup anonymously when the session is lost", async () => {
  expect(await resolveSetupProbeToken()).toBeNull();
  expect(request).not.toHaveBeenCalled();
});
it("uses the initial administrator JWT after wizard expiry", async () => {
  wizardSession.saveToken("expired");
  wizardSession.saveSetupJwt("admin-session");
  vi.mocked(request).mockResolvedValue({ valid: false });
  expect(await resolveSetupProbeToken()).toBe("admin-session");
  expect(request).toHaveBeenCalledTimes(1);
});
it("resume explicitly authenticates the administrator", () => {
  wizardApi.resumeWizard("admin-session");
  expect(request).toHaveBeenCalledWith("/setup/resume-wizard", {
    method: "POST",
    headers: { Authorization: "Bearer admin-session" },
  });
});
