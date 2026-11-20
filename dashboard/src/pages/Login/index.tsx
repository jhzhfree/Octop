import { useState, useEffect, useRef, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Input, Button, Checkbox } from "antd";
import { message } from "@/utils/antdMessage";

import { KeyRound, Lock, User } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  clearAuthToken,
  setAuthToken,
  setRememberLoginPreference,
} from "../../api";
import {
  authApi,
  type LdapStatus,
  type OauthProviderStatus,
} from "../../api/modules/auth";
import { apiErrorMessage } from "../../utils/apiError";
import { refreshServerLabels } from "../../i18n";
import { applyUserLocale, applyGuestLocale } from "../../utils/locale";
import styles from "./Login.module.css";
import {
  isSsoPopup,
  isSsoPopupMessage,
  notifySsoOpener,
  openSsoPopup,
} from "../../utils/ssoPopup";
import feishuIcon from "../../assets/channels/feishu.svg";
import dingtalkIcon from "../../assets/channels/dingtalk.svg";
import wecomIcon from "../../assets/channels/wecom.svg";
import googleIcon from "../../assets/providers/google.svg";
import CaptchaField, { type CaptchaFieldHandle } from "./CaptchaField";
import ForgotPasswordModal from "./ForgotPasswordModal";
import { type PublicCaptchaConfig } from "./captchaAdapters";

function providerLabel(
  provider: OauthProviderStatus,
  t: (key: string, opts?: Record<string, string>) => string,
): string {
  const name = provider.display_name.trim();
  if (name) return name;
  return t(`login.providerKind.${provider.kind}`, {
    defaultValue: provider.kind,
  });
}

function providerIcon(provider: OauthProviderStatus): ReactNode {
  if (provider.kind === "feishu") {
    return (
      <img src={feishuIcon} alt="" width={18} height={18} draggable={false} />
    );
  }
  if (provider.kind === "dingtalk") {
    return (
      <img src={dingtalkIcon} alt="" width={18} height={18} draggable={false} />
    );
  }
  if (provider.kind === "wecom") {
    return (
      <img src={wecomIcon} alt="" width={18} height={18} draggable={false} />
    );
  }
  const name = provider.display_name.trim().toLowerCase();
  if (
    provider.kind === "oidc" &&
    (name === "google" || name.includes("google"))
  ) {
    return (
      <img src={googleIcon} alt="" width={18} height={18} draggable={false} />
    );
  }
  return <KeyRound size={18} />;
}

export default function LoginPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [loading, setLoading] = useState(false);
  const [passwordLoginEnabled, setPasswordLoginEnabled] = useState(true);
  const [providers, setProviders] = useState<OauthProviderStatus[]>([]);
  const [ldap, setLdap] = useState<LdapStatus | null>(null);
  const [ssoLoadingKind, setSsoLoadingKind] = useState<string | null>(null);
  const [captchaReady, setCaptchaReady] = useState(false);
  const [captchaResetKey, setCaptchaResetKey] = useState(0);
  const [showForgotHelp, setShowForgotHelp] = useState(false);
  const [captcha, setCaptcha] = useState<PublicCaptchaConfig>({
    provider: "slider",
  });
  const captchaRef = useRef<CaptchaFieldHandle>(null);

  useEffect(() => {
    void applyGuestLocale();
  }, []);

  useEffect(() => {
    let cancelled = false;
    authApi
      .getAuthStatus()
      .then((status) => {
        if (cancelled) return;
        if (status.setup_required) {
          clearAuthToken();
          navigate("/setup", { replace: true });
          return;
        }
        // Only probe OIDC / captcha after setup is done — otherwise lockdown 503s.
        authApi
          .getOauthStatus()
          .then((next) => {
            if (!cancelled) {
              setProviders(next.providers.filter((item) => item.enabled));
              setPasswordLoginEnabled(next.password_login_enabled !== false);
            }
          })
          .catch(() => {});
        authApi
          .getLdapStatus()
          .then((next) => {
            if (!cancelled) setLdap(next);
          })
          .catch(() => {});
        authApi
          .getCaptcha()
          .then((next) => {
            if (!cancelled) setCaptcha(next);
          })
          .catch(() => {
            if (!cancelled) setCaptcha({ provider: "slider" });
          });
      })
      .catch(() => {
        // Backend unreachable — let the user attempt login and show a real
        // error from the request itself; redirecting blindly to /setup
        // would mask the actual problem.
      });
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  useEffect(() => {
    const code = searchParams.get("oidc_error");
    if (!code) return;
    if (notifySsoOpener({ ok: false, error: code })) return;
    message.error(
      t(`login.oidcError.${code}`, {
        defaultValue: t("login.oidcError.generic"),
      }),
    );
    navigate("/login", { replace: true });
  }, [navigate, searchParams, t]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (!isSsoPopupMessage(event, window.location.origin) || !event.data.ok) {
        if (
          isSsoPopupMessage(event, window.location.origin) &&
          !event.data.ok
        ) {
          setSsoLoadingKind(null);
          const code = event.data.error || "generic";
          message.error(
            t(`login.oidcError.${code}`, {
              defaultValue: t("login.oidcError.generic"),
            }),
          );
        }
        return;
      }
      if (event.data.access_token) {
        setAuthToken(event.data.access_token, event.data.remember ?? true);
      }
      window.location.replace(event.data.redirect || "/chat");
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [t]);

  const resetCaptcha = () => {
    setCaptchaReady(false);
    setCaptchaResetKey((k) => k + 1);
  };

  const onSso = async (kind: string) => {
    setSsoLoadingKind(kind);
    setRememberLoginPreference(remember);
    let popup: Window | null = null;
    if (kind !== "oidc") {
      popup = openSsoPopup();
    }
    try {
      const { authorization_url } = await authApi.startOauth(kind, "/chat");
      if (kind === "oidc") {
        window.location.href = authorization_url;
        return;
      }
      if (popup && !popup.closed) {
        popup.location.href = authorization_url;
        const timer = window.setInterval(() => {
          if (!popup || popup.closed) {
            window.clearInterval(timer);
            setSsoLoadingKind((current) => (current === kind ? null : current));
          }
        }, 400);
      } else {
        popup?.close();
        message.error(t("account.ssoPopupBlocked"));
        setSsoLoadingKind(null);
      }
    } catch (err) {
      popup?.close();
      message.error(apiErrorMessage(err, t("login.oidcStartFailed"), t));
      setSsoLoadingKind(null);
    }
  };

  const handleLogin = async () => {
    if (!username || !password || !captchaReady) return;
    setLoading(true);
    try {
      const token = await captchaRef.current?.getToken();
      const res = await authApi.login(username, password, token);
      setAuthToken(res.access_token, remember);
      await applyUserLocale(res.user.locale);
      void refreshServerLabels(res.user.locale);
      navigate("/chat", { replace: true });
    } catch (err) {
      message.error(apiErrorMessage(err, t("login.failed"), t));
      resetCaptcha();
    } finally {
      setLoading(false);
    }
  };

  if (isSsoPopup() && searchParams.get("oidc_error")) {
    return null;
  }

  return (
    <main className={styles.page}>
      <section className={styles.brand} aria-label={t("login.productName")}>
        <div className={styles.brandName}>
          <span className={styles.brandMark} aria-hidden="true">
            A
          </span>
          <div>
            <strong>AI-TAgent</strong>
            <span>{t("login.productName")}</span>
          </div>
        </div>
        <div className={styles.brandBody}>
          <p className={styles.eyebrow}>{t("login.brandEyebrow")}</p>
          <h1>{t("login.brandHeadline")}</h1>
          <p className={styles.description}>{t("login.brandDescription")}</p>
          <ul className={styles.points}>
            {[
              "brandPointAgents",
              "brandPointKnowledge",
              "brandPointControl",
            ].map((key) => (
              <li key={key}>
                <span aria-hidden="true">✓</span>
                {t(`login.${key}`)}
              </li>
            ))}
          </ul>
        </div>
        <p className={styles.brandFooter}>
          AI-TAgent · {t("login.productName")}
        </p>
      </section>
      <section className={styles.panel} aria-labelledby="login-heading">
        <div className={styles.card}>
          <header className={styles.cardHeader}>
            <h2 id="login-heading">{t("login.title")}</h2>
            <p>{t("login.subtitle")}</p>
          </header>
          {passwordLoginEnabled && (
            <>
              <Input
                prefix={
                  <User
                    size={16}
                    style={{ color: "var(--fn-text-quaternary)" }}
                  />
                }
                aria-label={t("login.username")}
                autoComplete="username"
                placeholder={t("login.username")}
                size="large"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
                style={{ borderRadius: 10 }}
              />

              <Input.Password
                prefix={
                  <Lock
                    size={16}
                    style={{ color: "var(--fn-text-quaternary)" }}
                  />
                }
                aria-label={t("login.password")}
                autoComplete="current-password"
                placeholder={t("login.password")}
                size="large"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onPressEnter={handleLogin}
                style={{ borderRadius: 10 }}
              />

              {ldap?.enabled && (
                <p
                  style={{
                    margin: 0,
                    marginTop: -8,
                    width: "100%",
                    fontSize: 12,
                    lineHeight: 1.5,
                    color: "var(--fn-text-tertiary)",
                    textAlign: "center",
                  }}
                >
                  {t("login.ldapHint", {
                    name: ldap.display_name.trim() || t("adminSso.ldap.kind"),
                  })}
                </p>
              )}

              <CaptchaField
                ref={captchaRef}
                config={captcha}
                resetKey={captchaResetKey}
                slideHint={t("login.slideHint")}
                slideVerifiedLabel={t("login.slideVerified")}
                unsupportedLabel={t("login.unsupportedCaptcha")}
                onReadyChange={setCaptchaReady}
              />

              <Button
                type="primary"
                size="large"
                block
                loading={loading}
                onClick={handleLogin}
                disabled={!username || !password || !captchaReady}
                style={{ borderRadius: 10, height: 44, fontWeight: 500 }}
              >
                {t("login.submit")}
              </Button>

              <div
                style={{
                  width: "100%",
                  display: "flex",
                  flexDirection: "column",
                  gap: 8,
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 12,
                    width: "100%",
                  }}
                >
                  <Checkbox
                    checked={remember}
                    onChange={(e) => setRemember(e.target.checked)}
                    style={{
                      margin: 0,
                      fontSize: 13,
                      color: "var(--fn-text-tertiary)",
                    }}
                  >
                    {t("login.remember")}
                  </Checkbox>
                  <button
                    type="button"
                    data-testid="login-forgot-password-toggle"
                    onClick={() => setShowForgotHelp(true)}
                    style={{
                      margin: 0,
                      padding: 0,
                      border: "none",
                      background: "none",
                      cursor: "pointer",
                      fontSize: 13,
                      lineHeight: 1.5,
                      color: "var(--fn-text-tertiary)",
                      flexShrink: 0,
                    }}
                  >
                    {t("login.forgotPassword", "Forgot password?")}
                  </button>
                </div>
                <ForgotPasswordModal
                  open={showForgotHelp}
                  onClose={() => setShowForgotHelp(false)}
                />
              </div>
            </>
          )}

          {providers.length > 0 && (
            <>
              {passwordLoginEnabled && (
                <div
                  style={{
                    width: "100%",
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                    color: "var(--fn-text-tertiary)",
                    fontSize: 13,
                  }}
                >
                  <span
                    style={{
                      flex: 1,
                      height: 1,
                      background: "var(--fn-border-primary)",
                    }}
                  />
                  {t("login.or")}
                  <span
                    style={{
                      flex: 1,
                      height: 1,
                      background: "var(--fn-border-primary)",
                    }}
                  />
                </div>
              )}
              {providers.map((provider) => (
                <Button
                  key={provider.kind}
                  size="large"
                  block
                  icon={providerIcon(provider)}
                  loading={ssoLoadingKind === provider.kind}
                  onClick={() => void onSso(provider.kind)}
                  style={{ borderRadius: 10, height: 44, fontWeight: 500 }}
                >
                  {t("login.oidcWith", { name: providerLabel(provider, t) })}
                </Button>
              ))}
            </>
          )}
        </div>
      </section>
    </main>
  );
}
