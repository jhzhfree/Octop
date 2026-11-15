import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
  type ReactNode,
} from "react";
import { Button, Result, Spin } from "antd";
import { useTranslation } from "react-i18next";
import {
  authApi,
  validAccessState,
  type AccessState,
  type OctopUser,
} from "../api/modules/auth";
import { startActivityRenewal } from "../api/activityRenewal";
import { CurrentUserProvider } from "../hooks/useCurrentUser";
import { SensitiveAccessContext } from "./SensitiveAccessBoundary";

export const ACCESS_INTERVAL_MS = 30_000;
export const ACCESS_TIMEOUT_MS = 10_000;
const RESUME_COOLDOWN_MS = 5_000;
interface Props {
  user: OctopUser | null;
  setUser: Dispatch<SetStateAction<OctopUser | null>>;
  children: ReactNode;
}
/** One monitor per protected application tree, independent of route navigation. */
export default function AccessSession({ user, setUser, children }: Props) {
  const { t } = useTranslation();
  const [state, setState] = useState<AccessState>();
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let alive = true,
      stopped = false,
      busy = false,
      generation = 0,
      lastStart = 0;
    let next: ReturnType<typeof setTimeout> | undefined;
    let deadline: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    const cancel = () => {
      generation++;
      busy = false;
      clearTimeout(next);
      clearTimeout(deadline);
      controller?.abort();
    };
    const fail = () => {
      cancel();
      stopped = true;
      if (alive) {
        setReady(false);
        setState(undefined);
        setFailed(true);
      }
    };
    const check = async () => {
      if (!alive || stopped || busy || document.hidden) return;
      if (!navigator.onLine) {
        fail();
        return;
      }
      busy = true;
      lastStart = Date.now();
      clearTimeout(next);
      const seq = ++generation;
      controller = new AbortController();
      deadline = setTimeout(fail, ACCESS_TIMEOUT_MS);
      try {
        const result = await authApi.accessState(controller.signal);
        if (!alive || seq !== generation) return;
        if (!validAccessState(result)) {
          fail();
          return;
        }
        clearTimeout(deadline);
        busy = false;
        setState((previous) =>
          previous?.revision === result.revision &&
          JSON.stringify(previous.user) === JSON.stringify(result.user)
            ? previous
            : result,
        );
        setReady(true);
        setFailed(false);
        next = setTimeout(() => void check(), ACCESS_INTERVAL_MS);
      } catch {
        if (alive && seq === generation) fail();
      }
    };
    const suspend = () => {
      cancel();
      if (alive) setReady(false);
    };
    const resume = () => {
      if (!alive || stopped || busy || document.hidden) return;
      clearTimeout(next);
      next = setTimeout(
        () => void check(),
        Math.max(0, RESUME_COOLDOWN_MS - (Date.now() - lastStart)),
      );
    };
    const visibility = () => {
      if (document.hidden) suspend();
      else resume();
    };
    const storage = (event: StorageEvent) => {
      if (event.key === "auth_token" || event.key === null) {
        suspend();
        resume();
      }
    };
    setFailed(false);
    setReady(false);
    setState(undefined);
    if (!navigator.onLine) fail();
    else void check();
    document.addEventListener("visibilitychange", visibility);
    window.addEventListener("offline", fail);
    window.addEventListener("focus", resume);
    window.addEventListener("pagehide", suspend);
    window.addEventListener("pageshow", resume);
    window.addEventListener("storage", storage);
    // Request interceptor dispatches this before navigating on expired/revoked sessions.
    window.addEventListener("octop:unauthorized", fail);
    return () => {
      alive = false;
      cancel();
      document.removeEventListener("visibilitychange", visibility);
      window.removeEventListener("offline", fail);
      window.removeEventListener("focus", resume);
      window.removeEventListener("pagehide", suspend);
      window.removeEventListener("pageshow", resume);
      window.removeEventListener("storage", storage);
      window.removeEventListener("octop:unauthorized", fail);
    };
  }, [attempt]);
  const renewalEnabled = useRef(false);
  renewalEnabled.current = ready && !failed;
  useEffect(() => startActivityRenewal(() => renewalEnabled.current), []);
  const verifiedUser = useMemo(() => {
    if (!state) return null;
    // Keep same-account profile edits responsive, but never take authority from them.
    const profile = user?.id === state.user.id ? user : {};
    return {
      ...state.user,
      ...profile,
      id: state.user.id,
      role: state.user.role,
      permissions: state.user.permissions,
    };
  }, [user, state]);
  if (failed)
    return (
      <Result
        status="warning"
        title={t("accessSession.failed")}
        subTitle={t("accessSession.notice")}
        extra={
          <Button onClick={() => setAttempt((n) => n + 1)}>
            {t("errors.retry")}
          </Button>
        }
      />
    );
  if (!state) return <Spin size="large" />;
  return (
    <CurrentUserProvider
      key={state.revision}
      user={verifiedUser}
      setUser={setUser}
    >
      <SensitiveAccessContext.Provider value={ready}>
        {children}
      </SensitiveAccessContext.Provider>
    </CurrentUserProvider>
  );
}
