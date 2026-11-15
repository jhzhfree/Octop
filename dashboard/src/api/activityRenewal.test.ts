import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  startActivityRenewal,
  ACTIVITY_RENEW_INTERVAL_MS,
} from "./activityRenewal";
import { clearAuthToken, getAuthToken, setAuthToken } from "./request";
vi.mock("./config", () => ({ getApiUrl: (path: string) => `/api${path}` }));
vi.mock("../i18n", () => ({ default: { language: "en" } }));
const renewedResponse = () =>
  new Response(null, {
    status: 204,
    headers: { "X-Octop-Access-Token": "renewed" },
  });
describe("activity-only renewal", () => {
  let stop: () => void;
  let activity: (event: Event) => void;
  let fetcher: ReturnType<typeof vi.fn>;
  const trusted = () => activity({ isTrusted: true } as Event);
  const flush = async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  };
  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
    sessionStorage.clear();
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    setAuthToken("old", false);
    fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetcher);
    const listener = vi.spyOn(document, "addEventListener");
    stop = startActivityRenewal();
    // Exercise the predicate with a synthetic flag, not a real browser gesture.
    activity = listener.mock.calls.find(
      ([name]) => name === "pointerdown",
    )![1] as (event: Event) => void;
  });
  afterEach(() => {
    stop();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });
  it("does not renew on mount, timers, focus, resume, or synthetic events", () => {
    vi.advanceTimersByTime(3_600_000);
    window.dispatchEvent(new Event("focus"));
    window.dispatchEvent(new Event("pageshow"));
    document.dispatchEvent(new Event("pointerdown"));
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "a" }));
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("uses explicit POST, throttles, and preserves storage mode", async () => {
    fetcher.mockResolvedValue(renewedResponse());
    trusted();
    trusted();
    await flush();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledWith(
      "/api/auth/renew",
      expect.objectContaining({
        method: "POST",
        headers: { Authorization: "Bearer old" },
      }),
    );
    expect(getAuthToken()).toBe("renewed");
    expect(localStorage.getItem("auth_token")).toBeNull();
    trusted();
    expect(fetcher).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(ACTIVITY_RENEW_INTERVAL_MS);
    trusted();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it.each(["logout", "new-login", "other-tab", "unmount", "hidden", "timeout"])(
    "ignores late renewal after %s",
    async (action) => {
      let resolve!: (value: Response) => void;
      fetcher.mockReturnValue(
        new Promise<Response>((done) => {
          resolve = done;
        }),
      );
      trusted();
      if (action === "logout") clearAuthToken();
      if (action === "new-login") setAuthToken("new-login");
      if (action === "other-tab")
        localStorage.setItem("auth_token", "other-tab");
      if (action === "unmount") stop();
      if (action === "hidden") {
        Object.defineProperty(document, "visibilityState", {
          configurable: true,
          value: "hidden",
        });
        document.dispatchEvent(new Event("visibilitychange"));
      }
      if (action === "timeout") vi.advanceTimersByTime(10_000);
      const expected = getAuthToken();
      resolve(renewedResponse());
      await flush();
      expect(getAuthToken()).toBe(expected);
    },
  );
  it("does not retry errors on a timer or renew from a hidden tab", async () => {
    fetcher.mockRejectedValue(new Error("offline"));
    trusted();
    await flush();
    vi.advanceTimersByTime(ACTIVITY_RENEW_INTERVAL_MS);
    expect(fetcher).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    trusted();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("does not write tokens from a failing response", async () => {
    fetcher.mockResolvedValue(
      new Response(null, {
        status: 401,
        headers: { "X-Octop-Access-Token": "bad" },
      }),
    );
    trusted();
    await flush();
    expect(getAuthToken()).toBe("old");
  });
  it("honors bounded server cadence for short TTL without a retry timer", async () => {
    fetcher.mockResolvedValue(
      new Response(null, {
        status: 204,
        headers: { "X-Octop-Renew-Check-After": "10" },
      }),
    );
    trusted();
    await flush();
    vi.advanceTimersByTime(10_000);
    expect(fetcher).toHaveBeenCalledTimes(1);
    trusted();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("suppresses requests and late results while access is unverified", async () => {
    stop();
    let enabled = false;
    const listener = vi.spyOn(document, "addEventListener");
    listener.mockClear();
    stop = startActivityRenewal(() => enabled);
    activity = listener.mock.calls.find(
      ([name]) => name === "pointerdown",
    )![1] as (event: Event) => void;
    trusted();
    expect(fetcher).not.toHaveBeenCalled();
    enabled = true;
    let resolve!: (response: Response) => void;
    fetcher.mockReturnValue(
      new Promise<Response>((done) => {
        resolve = done;
      }),
    );
    trusted();
    enabled = false;
    resolve(renewedResponse());
    await flush();
    expect(getAuthToken()).toBe("old");
  });
  it("removes activity listeners on unmount", () => {
    const remove = vi.spyOn(document, "removeEventListener");
    stop();
    trusted();
    expect(fetcher).not.toHaveBeenCalled();
    expect(remove).toHaveBeenCalledWith("pointerdown", activity, true);
  });
});
