import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AuthGuard from "./AuthGuard";
import { authApi } from "../api/modules/auth";
import { clearAuthToken, getAuthToken, setAuthToken } from "../api/request";
import { applyUserLocale } from "../utils/locale";
vi.mock("../api/modules/auth", async (original) => ({
  ...(await original<typeof import("../api/modules/auth")>()),
  authApi: { getAuthStatus: vi.fn(), me: vi.fn(), accessState: vi.fn() },
}));
vi.mock("../utils/locale", () => ({ applyUserLocale: vi.fn() }));
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
const status = { setup_required: false, has_admin: true };
const me = { user_id: 2, username: "current", role: "user", locale: "en" };
function mount() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<AuthGuard>protected-shell</AuthGuard>} />
        <Route path="/setup" element={<>unexpected-setup</>} />
        <Route path="/login" element={<>unexpected-login</>} />
      </Routes>
    </MemoryRouter>,
  );
}
describe("boot response session ownership", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    setAuthToken("old");
    vi.mocked(authApi.getAuthStatus).mockResolvedValue(status as never);
    vi.mocked(authApi.me).mockResolvedValue(me as never);
    vi.mocked(applyUserLocale).mockResolvedValue(undefined);
    vi.mocked(authApi.accessState).mockResolvedValue({
      revision: "a".repeat(64),
      user: {
        id: 2,
        username: "current",
        role: "user",
        permissions: [],
        display_name: null,
        locale: "en",
      },
    });
  });
  it.each(["different-token", "same-token", "other-tab"])(
    "rechecks stale setup status: %s",
    async (change) => {
      const pending =
        deferred<Awaited<ReturnType<typeof authApi.getAuthStatus>>>();
      vi.mocked(authApi.getAuthStatus).mockReturnValueOnce(pending.promise);
      mount();
      await act(async () => {
        if (change === "other-tab") localStorage.setItem("auth_token", "new");
        else {
          clearAuthToken();
          setAuthToken(change === "same-token" ? "old" : "new");
        }
        pending.resolve({ setup_required: true, has_admin: false } as never);
      });
      expect(await screen.findByText("protected-shell")).toBeInTheDocument();
      expect(getAuthToken()).toBe(change === "same-token" ? "old" : "new");
      expect(authApi.getAuthStatus).toHaveBeenCalledTimes(2);
      expect(screen.queryByText("unexpected-setup")).not.toBeInTheDocument();
    },
  );
  it.each(["success", "failure"])("discards stale /me %s", async (outcome) => {
    const pending = deferred<Awaited<ReturnType<typeof authApi.me>>>();
    vi.mocked(authApi.me).mockReturnValueOnce(pending.promise);
    mount();
    await waitFor(() => expect(authApi.me).toHaveBeenCalledOnce());
    await act(async () => {
      setAuthToken("new");
      if (outcome === "success")
        pending.resolve({ ...me, user_id: 1, locale: "zh" } as never);
      else pending.reject(new Error("old unauthorized"));
    });
    expect(await screen.findByText("protected-shell")).toBeInTheDocument();
    expect(applyUserLocale).toHaveBeenCalledExactlyOnceWith("en");
    expect(screen.queryByText("unexpected-login")).not.toBeInTheDocument();
  });
  it("rechecks after session changes during locale loading", async () => {
    const pending = deferred<void>();
    vi.mocked(applyUserLocale).mockReturnValueOnce(pending.promise);
    mount();
    await waitFor(() => expect(applyUserLocale).toHaveBeenCalledOnce());
    await act(async () => {
      setAuthToken("new");
      pending.resolve();
    });
    expect(await screen.findByText("protected-shell")).toBeInTheDocument();
    expect(authApi.me).toHaveBeenCalledTimes(2);
    expect(authApi.accessState).toHaveBeenCalledOnce();
  });
});
