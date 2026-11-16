import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./config", () => ({
  getApiUrl: (path: string) => `/api${path}`,
}));

vi.mock("../i18n", () => ({
  default: { language: "zh" },
}));

function setupRequiredResponse(): Response {
  return new Response(JSON.stringify({ setup_required: true }), {
    status: 503,
    headers: { "content-type": "application/json" },
  });
}

describe("setup lockdown handling", () => {
  const replace = vi.fn();
  let mod: typeof import("./request");
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(async () => {
    vi.resetModules();
    localStorage.clear();
    sessionStorage.clear();
    replace.mockClear();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { pathname: "/chat", replace },
    });
    fetchMock = vi.fn(() => Promise.resolve(setupRequiredResponse()));
    vi.stubGlobal("fetch", fetchMock);
    mod = await import("./request");
    mod.setAuthToken("stale-token");
    // setAuthToken clears the setup flag — re-arm a clean slate for each case.
    mod.clearSetupRequired();
  });

  it.each([true, false])(
    "ignores stale successful setup flags: %s",
    async (required) => {
      let finish!: (response: Response) => void;
      fetchMock.mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            finish = resolve;
          }),
      );
      const { authApi } = await import("./modules/auth");
      const pending = authApi.getAuthStatus();
      mod.setAuthToken("new-session");
      if (!required) mod.markSetupRequired();
      finish(
        new Response(JSON.stringify({ setup_required: required }), {
          headers: { "content-type": "application/json" },
        }),
      );
      await pending;
      expect(mod.getAuthToken()).toBe("new-session");
      expect(mod.isSetupRequiredKnown()).toBe(!required);
    },
  );

  it("does not share old-session probes or erase a newer in-flight probe", async () => {
    const finish: Array<(response: Response) => void> = [];
    fetchMock.mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          finish.push(resolve);
        }),
    );
    const { authApi } = await import("./modules/auth");
    const old = authApi.getAuthStatus();
    mod.setAuthToken("new-session");
    const current = authApi.getAuthStatus();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const response = (required: boolean) =>
      new Response(JSON.stringify({ setup_required: required }), {
        headers: { "content-type": "application/json" },
      });
    finish[0](response(true));
    await old;
    const shared = authApi.getAuthStatus();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    finish[1](response(false));
    expect((await current).setup_required).toBe(false);
    expect((await shared).setup_required).toBe(false);
    expect(mod.isSetupRequiredKnown()).toBe(false);
  });

  it("redirects to /setup and clears the token on lockdown 503", async () => {
    await expect(mod.request("/agents")).rejects.toThrow(
      mod.SetupRequiredError,
    );

    expect(replace).toHaveBeenCalledWith("/setup");
    expect(mod.getAuthToken()).toBe("");
    expect(mod.isSetupRequiredKnown()).toBe(true);
  });

  it("skips the network after setup lockdown is known", async () => {
    await expect(mod.request("/agents")).rejects.toThrow(
      mod.SetupRequiredError,
    );
    fetchMock.mockClear();
    replace.mockClear();

    await expect(mod.request("/settings/capabilities")).rejects.toThrow(
      mod.SetupRequiredError,
    );

    expect(fetchMock).not.toHaveBeenCalled();
    // Already redirected once — no second navigation.
    expect(replace).not.toHaveBeenCalled();
  });

  it("still allows /setup/status while lockdown is known", async () => {
    mod.markSetupRequired();
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          setup_required: true,
          wizard_password_required: true,
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );

    const body = await mod.request<{ setup_required: boolean }>(
      "/setup/status",
    );
    expect(body.setup_required).toBe(true);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("does not hard-reload when already on /setup", async () => {
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { pathname: "/setup", replace },
    });

    await expect(mod.request("/agents")).rejects.toThrow(
      mod.SetupRequiredError,
    );
    expect(replace).not.toHaveBeenCalled();
  });
});
