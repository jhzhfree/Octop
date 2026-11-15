import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useEffect, useState } from "react";
import AccessSession, {
  ACCESS_INTERVAL_MS,
  ACCESS_TIMEOUT_MS,
} from "./AccessSession";
import SensitiveAccessBoundary from "./SensitiveAccessBoundary";
import { authApi, type AccessState } from "../api/modules/auth";
import { useCurrentUser } from "../hooks/useCurrentUser";
vi.mock("../api/modules/auth", async (original) => ({
  ...(await original<typeof import("../api/modules/auth")>()),
  authApi: { accessState: vi.fn() },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const good: AccessState = {
  revision: "a".repeat(64),
  user: {
    id: 1,
    username: "fixture",
    display_name: null,
    role: "user",
    permissions: ["tasks"],
    locale: "en",
  },
};
const identityChange = vi.fn();
const mount = vi.fn(),
  unmount = vi.fn();
function Probe() {
  useEffect(() => {
    mount();
    return () => {
      unmount();
    };
  }, []);
  const user = useCurrentUser();
  useEffect(() => {
    identityChange();
  }, [user]);
  const [opened, setOpened] = useState(false);
  return (
    <>
      <span>shell {user?.permissions?.join(",")}</span>
      <button onClick={() => setOpened(true)}>read</button>
      {opened && <div>PRIVATE REPORT</div>}
    </>
  );
}
function App() {
  return (
    <AccessSession user={good.user} setUser={vi.fn()}>
      <span>ordinary shell</span>
      <SensitiveAccessBoundary>
        <Probe />
      </SensitiveAccessBoundary>
    </AccessSession>
  );
}
const flush = async () => {
  await act(async () => {});
};
const advance = async (ms: number) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};
beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
  vi.mocked(authApi.accessState).mockReset().mockResolvedValue(good);
  identityChange.mockClear();
  mount.mockClear();
  unmount.mockClear();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});
it("uses one check for the whole tree; unchanged checks preserve opened content", async () => {
  render(<App />);
  await flush();
  fireEvent.click(screen.getByText("read"));
  await advance(ACCESS_INTERVAL_MS);
  expect(authApi.accessState).toHaveBeenCalledTimes(2);
  expect(screen.getByText("PRIVATE REPORT")).toBeTruthy();
  expect(mount).toHaveBeenCalledTimes(1);
  expect(identityChange).toHaveBeenCalledTimes(1);
});
it.each(["account", "permission", "organization", "role"])(
  "destroys old contents when server revision changes for %s",
  async (kind) => {
    render(<App />);
    await flush();
    fireEvent.click(screen.getByText("read"));
    vi.mocked(authApi.accessState).mockResolvedValue({
      ...good,
      revision: "b".repeat(64),
      user: {
        ...good.user,
        id: kind === "account" ? 2 : 1,
        permissions: kind === "permission" ? [] : ["tasks"],
        role: kind === "role" ? "admin" : "user",
      },
    });
    await advance(ACCESS_INTERVAL_MS);
    expect(screen.queryByText("PRIVATE REPORT")).toBeNull();
    expect(unmount).toHaveBeenCalledTimes(1);
  },
);
it.each(["offline", "error", "malformed", "timeout", "unauthorized"])(
  "fails closed on %s and requires an explicit retry",
  async (kind) => {
    let resolve!: (value: AccessState) => void;
    render(<App />);
    await flush();
    fireEvent.click(screen.getByText("read"));
    if (kind === "offline" || kind === "unauthorized")
      fireEvent(
        window,
        new Event(kind === "offline" ? "offline" : "octop:unauthorized"),
      );
    else {
      if (kind === "error")
        vi.mocked(authApi.accessState).mockRejectedValueOnce(
          new Error("SECRET"),
        );
      if (kind === "malformed")
        vi.mocked(authApi.accessState).mockResolvedValueOnce({} as AccessState);
      if (kind === "timeout")
        vi.mocked(authApi.accessState).mockImplementationOnce(
          () =>
            new Promise((r) => {
              resolve = r;
            }),
        );
      await advance(ACCESS_INTERVAL_MS);
      if (kind === "timeout") await advance(ACCESS_TIMEOUT_MS);
    }
    expect(screen.queryByText("PRIVATE REPORT")).toBeNull();
    expect(screen.queryByText("ordinary shell")).toBeNull();
    expect(screen.getByText("accessSession.failed")).toBeTruthy();
    if (resolve) {
      await act(async () => resolve(good));
      expect(screen.queryByText("ordinary shell")).toBeNull();
    }
    const count = vi.mocked(authApi.accessState).mock.calls.length;
    fireEvent.focus(window);
    await advance(60_000);
    expect(authApi.accessState).toHaveBeenCalledTimes(count);
    fireEvent.click(screen.getByText("errors.retry"));
    await flush();
    expect(screen.queryByText("PRIVATE REPORT")).toBeNull();
    expect(screen.getByText("ordinary shell")).toBeTruthy();
  },
);
it("hides sensitive views while hidden, pauses checks, and rechecks once on return", async () => {
  render(<App />);
  await flush();
  fireEvent.click(screen.getByText("read"));
  vi.spyOn(document, "hidden", "get").mockReturnValue(true);
  fireEvent(document, new Event("visibilitychange"));
  expect(screen.queryByText("PRIVATE REPORT")).toBeNull();
  expect(screen.getByText("ordinary shell")).toBeTruthy();
  await advance(120_000);
  expect(authApi.accessState).toHaveBeenCalledTimes(1);
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  for (let i = 0; i < 20; i++) {
    fireEvent(document, new Event("visibilitychange"));
    fireEvent.focus(window);
  }
  await advance(0);
  expect(authApi.accessState).toHaveBeenCalledTimes(2);
  expect(screen.queryByText("PRIVATE REPORT")).toBeNull();
});
it("aborts a suspended check and ignores its late response after new authority", async () => {
  let resolve!: (value: AccessState) => void;
  vi.mocked(authApi.accessState).mockImplementationOnce(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  const view = render(<App />);
  await flush();
  const signal = vi.mocked(authApi.accessState).mock.calls[0][0];
  vi.spyOn(document, "hidden", "get").mockReturnValue(true);
  fireEvent(document, new Event("visibilitychange"));
  expect(signal?.aborted).toBe(true);
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  fireEvent(document, new Event("visibilitychange"));
  await advance(5000);
  expect(screen.getByText("ordinary shell")).toBeTruthy();
  await act(async () =>
    resolve({ ...good, user: { ...good.user, permissions: [] } }),
  );
  expect(screen.getByText("shell tasks")).toBeTruthy();
  view.unmount();
  const count = vi.mocked(authApi.accessState).mock.calls.length;
  fireEvent.focus(window);
  await advance(120_000);
  expect(authApi.accessState).toHaveBeenCalledTimes(count);
});
