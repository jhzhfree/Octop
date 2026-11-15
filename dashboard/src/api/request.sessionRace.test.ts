import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./config", () => ({ getApiUrl: (path: string) => `/api${path}` }));
vi.mock("../i18n", () => ({ default: { language: "en" } }));

const xhrState: { current?: DeferredXhr } = {};
class DeferredXhr {
  status = 0;
  responseText = "";
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  upload = { onprogress: null };
  open = vi.fn();
  send = vi.fn();
  abort = vi.fn();
  setRequestHeader = vi.fn();
  getResponseHeader = vi.fn(() => null);
  constructor() {
    xhrState.current = this;
  }
  finish(status: number) {
    this.status = status;
    this.responseText = status === 503 ? '{"setup_required":true}' : "{}";
    this.onload?.();
  }
}

function response(status: number) {
  return new Response(status === 503 ? '{"setup_required":true}' : "{}", {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const transports = ["json", "blob", "probe", "stream", "upload"] as const;
type Transport = (typeof transports)[number];

describe("response ownership across login changes", () => {
  let api: typeof import("./request");
  let finish: (response: Response) => void;
  const replace = vi.fn();

  beforeEach(async () => {
    vi.resetModules();
    localStorage.clear();
    sessionStorage.clear();
    replace.mockClear();
    xhrState.current = undefined;
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { pathname: "/chat", replace },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            finish = resolve;
          }),
      ),
    );
    vi.stubGlobal("XMLHttpRequest", DeferredXhr);
    api = await import("./request");
    api.setAuthToken("old");
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function send(kind: Transport, headers?: HeadersInit) {
    const options = { headers };
    if (kind === "blob") return api.requestBlob("/files", options);
    if (kind === "probe") return api.probeAuthResource("/files", options);
    if (kind === "stream") return api.requestStream("/tts", options);
    if (kind === "upload")
      return api.requestUpload("/files", new FormData(), options);
    return api.request("/records", options);
  }
  function settle(kind: Transport, status: number) {
    if (kind === "upload") xhrState.current!.finish(status);
    else finish(response(status));
  }

  it.each(
    transports.flatMap((kind) =>
      [401, 503].map((status) => ({ kind, status })),
    ),
  )(
    "$kind late $status leaves a new login and active agent intact",
    async ({ kind, status }) => {
      const events = vi.spyOn(window, "dispatchEvent");
      const rejection = expect(send(kind)).rejects.toThrow();
      api.setAuthToken("new", false);
      api.setActiveAgentId("new-agent");
      localStorage.setItem("octop:active-agent", "new-agent");
      settle(kind, status);
      await rejection;
      expect(api.getAuthToken()).toBe("new");
      expect(api.isSessionOnlyAuth()).toBe(true);
      expect(api.getActiveAgentId()).toBe("new-agent");
      expect(localStorage.getItem("octop:active-agent")).toBe("new-agent");
      expect(api.isSetupRequiredKnown()).toBe(false);
      expect(replace).not.toHaveBeenCalled();
      expect(events).not.toHaveBeenCalled();
    },
  );

  it.each([401, 503])(
    "equal-token logout/login rejects stale %s without clearing the new generation",
    async (status) => {
      const rejection = expect(send("json")).rejects.toThrow();
      api.clearAuthToken();
      api.setAuthToken("old");
      settle("json", status);
      await rejection;
      expect(api.getAuthToken()).toBe("old");
      expect(api.isSetupRequiredKnown()).toBe(false);
      expect(replace).not.toHaveBeenCalled();
    },
  );

  it.each(transports)(
    "%s current 401 still clears and redirects",
    async (kind) => {
      const rejection = expect(send(kind)).rejects.toThrow();
      settle(kind, 401);
      await rejection;
      expect(api.getAuthToken()).toBe("");
      expect(replace).toHaveBeenCalledWith("/login");
    },
  );

  it.each(transports)(
    "%s explicit different authorization cannot invalidate the stored session",
    async (kind) => {
      const rejection = expect(
        send(kind, new Headers({ authorization: "Bearer wizard" })),
      ).rejects.toThrow();
      settle(kind, 401);
      await rejection;
      expect(api.getAuthToken()).toBe("old");
      expect(replace).not.toHaveBeenCalled();
      if (kind === "upload")
        expect(xhrState.current!.setRequestHeader).toHaveBeenCalledWith(
          "authorization",
          "Bearer wizard",
        );
      else
        expect(
          new Headers(vi.mocked(fetch).mock.calls[0][1]?.headers).get(
            "authorization",
          ),
        ).toBe("Bearer wizard");
    },
  );

  it("checks ownership after asynchronous 503 body parsing", async () => {
    let parsed!: (body: unknown) => void;
    const body = new Promise((resolve) => {
      parsed = resolve;
    });
    const reply = response(503);
    const clone = vi
      .spyOn(reply, "clone")
      .mockReturnValue({ json: () => body } as Response);
    const rejection = expect(send("json")).rejects.toThrow();
    finish(reply);
    await vi.waitFor(() => expect(clone).toHaveBeenCalled());
    api.setAuthToken("new");
    parsed({ setup_required: true });
    await rejection;
    expect(api.getAuthToken()).toBe("new");
    expect(api.isSetupRequiredKnown()).toBe(false);
  });

  it("observes a changed token written by another tab", async () => {
    const rejection = expect(send("json")).rejects.toThrow();
    localStorage.setItem("auth_token", "other-tab");
    settle("json", 401);
    await rejection;
    expect(api.getAuthToken()).toBe("other-tab");
    expect(replace).not.toHaveBeenCalled();
  });

  it("a new login re-arms unauthorized navigation", async () => {
    let rejection = expect(send("json")).rejects.toThrow();
    settle("json", 401);
    await rejection;
    api.setAuthToken("new");
    rejection = expect(send("json")).rejects.toThrow();
    settle("json", 401);
    await rejection;
    expect(replace).toHaveBeenCalledTimes(2);
  });
  it.each(transports)(
    "%s current setup lockdown still clears the session",
    async (kind) => {
      const rejection = expect(send(kind)).rejects.toThrow(
        api.SetupRequiredError,
      );
      settle(kind, 503);
      await rejection;
      expect(api.getAuthToken()).toBe("");
      expect(api.isSetupRequiredKnown()).toBe(true);
      expect(replace).toHaveBeenCalledWith("/setup");
    },
  );

  it("an anonymous old request cannot clear a later login", async () => {
    api.clearAuthToken();
    const rejection = expect(send("json")).rejects.toThrow();
    api.setAuthToken("new");
    settle("json", 401);
    await rejection;
    expect(api.getAuthToken()).toBe("new");
    expect(replace).not.toHaveBeenCalled();
  });
});
