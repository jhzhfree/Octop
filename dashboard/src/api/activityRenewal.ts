import { getApiUrl } from "./config";
import {
  applyRenewedAccessToken,
  captureAuthSession,
  isCurrentAuthSession,
} from "./request";

export const ACTIVITY_RENEW_INTERVAL_MS = 60_000;
export const ACTIVITY_RENEW_TIMEOUT_MS = 10_000;

/** Client UX signal only, never server-side proof of human presence. No heartbeat. */
export function startActivityRenewal(
  enabled: () => boolean = () => true,
): () => void {
  let alive = true;
  let lastAttempt = -Infinity;
  let interval = ACTIVITY_RENEW_INTERVAL_MS;
  let session = captureAuthSession();
  let controller: AbortController | undefined;
  let deadline: ReturnType<typeof setTimeout> | undefined;
  const cancel = () => {
    controller?.abort();
    controller = undefined;
    clearTimeout(deadline);
  };
  const activity = (event: Event) => {
    if (
      !alive ||
      !enabled() ||
      !event.isTrusted ||
      document.visibilityState !== "visible" ||
      (event instanceof KeyboardEvent && event.repeat)
    )
      return;
    if (!isCurrentAuthSession(session)) {
      cancel();
      session = captureAuthSession();
      interval = ACTIVITY_RENEW_INTERVAL_MS;
      lastAttempt = -Infinity;
    }
    if (controller || performance.now() - lastAttempt < interval) return;
    const expectedSession = session;
    if (!expectedSession.token) return;
    lastAttempt = performance.now();
    const attempt = new AbortController();
    controller = attempt;
    deadline = setTimeout(() => attempt.abort(), ACTIVITY_RENEW_TIMEOUT_MS);
    void fetch(getApiUrl("/auth/renew"), {
      method: "POST",
      headers: { Authorization: `Bearer ${expectedSession.token}` },
      signal: attempt.signal,
      cache: "no-store",
    })
      .then((response) => {
        if (
          alive &&
          enabled() &&
          !attempt.signal.aborted &&
          document.visibilityState === "visible" &&
          isCurrentAuthSession(expectedSession)
        ) {
          if (response.ok) {
            const seconds = Number(
              response.headers.get("X-Octop-Renew-Check-After"),
            );
            if (Number.isInteger(seconds) && seconds >= 1 && seconds <= 60) {
              interval = seconds * 1000;
            }
          }
          if (response.status === 429) {
            const seconds = Number(response.headers.get("Retry-After"));
            if (Number.isInteger(seconds) && seconds >= 1) {
              interval = Math.min(seconds, 3600) * 1000;
              lastAttempt = performance.now();
            }
          }
          if (applyRenewedAccessToken(response, expectedSession)) {
            // Our successful renewal keeps its learned cadence; a separate login
            // resets it on the next activity instead of inheriting old backoff.
            session = captureAuthSession();
          }
        }
      })
      .catch(() => {
        // No retry timer: AccessSession independently fails closed on invalid access.
      })
      .finally(() => {
        if (controller === attempt) cancel();
      });
  };
  const visibility = () => {
    if (document.visibilityState !== "visible") cancel();
  };
  document.addEventListener("pointerdown", activity, true);
  document.addEventListener("keydown", activity, true);
  document.addEventListener("visibilitychange", visibility);
  window.addEventListener("pagehide", cancel);
  return () => {
    alive = false;
    cancel();
    document.removeEventListener("pointerdown", activity, true);
    document.removeEventListener("keydown", activity, true);
    document.removeEventListener("visibilitychange", visibility);
    window.removeEventListener("pagehide", cancel);
  };
}
