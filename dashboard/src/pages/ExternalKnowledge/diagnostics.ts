import type { TFunction } from "i18next";

const hints: Record<string, string> = {
  INVALID_ARGUMENT: "configuration",
  EXTERNAL_KNOWLEDGE_INVALID_CONFIG: "configuration",
  NOT_FOUND: "notFound",
  INVALID_JSON: "protocol",
  EXTERNAL_KNOWLEDGE_PROTOCOL: "protocol",
  PROBE_FAILED: "capability",
  UPSTREAM_ERROR: "upstream",
  EXTERNAL_KNOWLEDGE_UNAVAILABLE: "upstream",
  INVALID_CREDENTIALS: "credentials",
  CONNECTOR_INVALID_CREDENTIALS: "credentials",
  AGENT_IDENTITY_NOT_READY: "identity",
  AGENT_IDENTITY_INVALID: "identity",
  FORBIDDEN: "permissions",
  KNOWLEDGE_FORBIDDEN: "permissions",
  TIMEOUT: "timeout",
  EXTERNAL_KNOWLEDGE_TIMEOUT: "timeout",
  RATE_LIMITED: "rate",
  UPSTREAM_UNAVAILABLE: "upstream",
  TRANSPORT_ERROR: "upstream",
};
export function checkHint(
  code: string,
  t: TFunction,
  status = "failed",
): string {
  if (status === "success") return t("externalKnowledge.diagnostics.passed");
  return t(`externalKnowledge.diagnostics.${hints[code] || "recheck"}`);
}
