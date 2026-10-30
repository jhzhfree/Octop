import type {
  IdentityConfiguration,
  SourceIdentityState,
} from "@/api/modules/agentIdentity";
const names = [
  "format",
  "proofProfile",
  "platformId",
  "installationId",
  "workspaceId",
  "resource",
  "policyId",
  "applicationPrincipalId",
  "issuer",
  "audience",
];
export class SetupFileError extends Error {
  readonly reason: "invalid" | "mismatch";
  constructor(reason: "invalid" | "mismatch") {
    super(reason);
    this.reason = reason;
  }
}
export function platformSetup(state: SourceIdentityState) {
  return {
    format: "akb-platform-setup/v1",
    proofProfile: "AGENT_KEY_RS256_V2",
    platformId: state.platformId,
    installationId: state.installationId,
    workspaceId: state.workspaceId,
    resource: state.resource,
    issuer: state.issuer,
    audience: state.audience,
  };
}
export function readConnection(
  text: string,
  state: SourceIdentityState,
): Omit<IdentityConfiguration, "mode"> {
  if (text.length > 16384) throw new SetupFileError("invalid");
  let value: Record<string, unknown>;
  try {
    value = JSON.parse(text);
  } catch {
    throw new SetupFileError("invalid");
  }
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).some((k) => !names.includes(k)) ||
    value.format !== "akb-platform-connection/v1" ||
    value.proofProfile !== "AGENT_KEY_RS256_V2"
  )
    throw new SetupFileError("invalid");
  for (const k of [
    "platformId",
    "installationId",
    "workspaceId",
    "resource",
  ] as const)
    if (value[k] !== state[k]) throw new SetupFileError("mismatch");
  if (
    typeof value.policyId !== "string" ||
    typeof value.applicationPrincipalId !== "string" ||
    !/^[1-9][0-9]{0,18}$/.test(String(value.policyId)) ||
    !/^[1-9][0-9]{0,18}$/.test(String(value.applicationPrincipalId)) ||
    typeof value.issuer !== "string" ||
    value.issuer.length > 255 ||
    typeof value.audience !== "string" ||
    !value.audience.trim() ||
    value.audience.length > 128 ||
    [...value.audience].some(
      (char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127,
    )
  )
    throw new SetupFileError("invalid");
  try {
    const issuer = new URL(value.issuer);
    if (
      issuer.protocol !== "https:" ||
      issuer.username ||
      issuer.password ||
      issuer.search ||
      issuer.hash
    )
      throw new Error();
  } catch {
    throw new SetupFileError("invalid");
  }
  return {
    policyId: String(value.policyId),
    applicationPrincipalId: String(value.applicationPrincipalId),
    issuer: value.issuer,
    audience: value.audience,
  };
}
