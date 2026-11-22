import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

const login = vi.fn();
const setAuthToken = vi.fn();
const getOauthStatus = vi.fn();
vi.mock("../../api", () => ({
  clearAuthToken: vi.fn(),
  setAuthToken: (...args: unknown[]) => setAuthToken(...args),
  setRememberLoginPreference: vi.fn(),
}));
vi.mock("../../api/modules/auth", () => ({
  authApi: {
    getAuthStatus: () => Promise.resolve({ setup_required: false }),
    getOauthStatus: () => getOauthStatus(),
    getLdapStatus: () => Promise.resolve({ enabled: false }),
    getCaptcha: () => Promise.resolve({ provider: "none" }),
    login: (...args: unknown[]) => login(...args),
  },
}));
vi.mock("../../utils/locale", () => ({
  applyGuestLocale: () => Promise.resolve(),
  applyUserLocale: () => Promise.resolve(),
}));
vi.mock("../../i18n", () => ({ refreshServerLabels: vi.fn() }));
vi.mock("./CaptchaField", async () => {
  const React = await import("react");
  return {
    default: React.forwardRef(function TestCaptcha(
      props: { onReadyChange: (ready: boolean) => void },
      ref,
    ) {
      React.useEffect(() => props.onReadyChange(true), [props.onReadyChange]);
      React.useImperativeHandle(ref, () => ({
        getToken: async () => "test-captcha",
      }));
      return null;
    }),
  };
});
import LoginPage from "./index";

describe("WorkAssistant login layout", () => {
  it("shows the brand and accessible credentials while preserving login", async () => {
    getOauthStatus.mockResolvedValue({ providers: [] });
    login.mockResolvedValue({
      access_token: "test-token",
      user: { locale: "en" },
    });
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 1, name: "login.productName" }),
    ).toBeInTheDocument();
    expect(screen.queryByAltText("Octop")).not.toBeInTheDocument();
    const username = screen.getByRole("textbox", { name: "login.username" });
    const password = screen.getByLabelText("login.password");
    expect(username).toHaveAttribute("autocomplete", "username");
    expect(password).toHaveAttribute("autocomplete", "current-password");
    const user = userEvent.setup();
    await user.type(username, "alice");
    await user.type(password, "test-password");
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "login.submit" }),
      ).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "login.submit" }));
    await waitFor(() =>
      expect(login).toHaveBeenCalledWith(
        "alice",
        "test-password",
        "test-captcha",
      ),
    );
    expect(setAuthToken).toHaveBeenCalledWith("test-token", true);
  });

  it("keeps unified identity visible without showing the password form", async () => {
    getOauthStatus.mockResolvedValue({
      password_login_enabled: false,
      providers: [{ kind: "oidc", display_name: "Company SSO", enabled: true }],
    });
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );
    expect(
      await screen.findByRole("button", { name: "login.oidcWith" }),
    ).toBeEnabled();
    expect(
      screen.queryByRole("textbox", { name: "login.username" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("login.password")).not.toBeInTheDocument();
  });
});
