import { getApiUrl } from "./config";
import { applyRenewedAccessToken, getAuthToken } from "./request";

export const ACTIVITY_RENEW_INTERVAL_MS = 60_000;
export const ACTIVITY_RENEW_TIMEOUT_MS = 10_000;

/** Client UX signal only, never server-side proof of human presence. No heartbeat. */
export function startActivityRenewal(
  enabled: () => boolean = () => true,
): () => void {
  let alive = true;
  let lastAttempt = -Infinity;
  let interval = ACTIVITY_RENEW_INTERVAL_MS;
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
      (event instanceof KeyboardEvent && event.repeat) ||
      controller ||
      performance.now() - lastAttempt < interval
    )
      return;
    const expectedToken = getAuthToken();
    if (!expectedToken) return;
    lastAttempt = performance.now();
    const attempt = new AbortController();
    controller = attempt;
    deadline = setTimeout(() => attempt.abort(), ACTIVITY_RENEW_TIMEOUT_MS);
    void fetch(getApiUrl("/auth/renew"), {
      method: "POST",
      headers: { Authorization: `Bearer ${expectedToken}` },
      signal: attempt.signal,
      cache: "no-store",
    })
      .then((response) => {
        if (
          alive &&
          enabled() &&
          !attempt.signal.aborted &&
          document.visibilityState === "visible"
        ) {
          if (response.ok && getAuthToken() === expectedToken) {
            const seconds = Number(
              response.headers.get("X-Octop-Renew-Check-After"),
            );
            if (Number.isInteger(seconds) && seconds >= 1 && seconds <= 60) {
              interval = seconds * 1000;
            }
          }
          applyRenewedAccessToken(response, expectedToken);
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
