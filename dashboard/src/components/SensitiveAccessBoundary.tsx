import { createContext, useContext, type ReactNode } from "react";

/** UI lifetime only. Every read/write still requires server authorization. */
export const SensitiveAccessContext = createContext(true);
export default function SensitiveAccessBoundary({
  children,
}: {
  children: ReactNode;
}) {
  const ready = useContext(SensitiveAccessContext);
  return ready ? <>{children}</> : null;
}
