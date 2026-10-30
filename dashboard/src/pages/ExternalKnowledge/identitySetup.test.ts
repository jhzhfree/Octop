import { expect, it } from "vitest";
import type { SourceIdentityState } from "@/api/modules/agentIdentity";
import { platformSetup, readConnection } from "./identitySetup";
const state = {
  platformId: "octop",
  installationId: "install",
  workspaceId: "workspace",
  resource: "https://akb.example",
  issuer: "https://octop.local/agents",
  audience: "akb-external-api",
} as SourceIdentityState;
const connection = {
  ...platformSetup(state),
  format: "akb-platform-connection/v1",
  policyId: "17",
  applicationPrincipalId: "123",
};
it("imports public connection values without enabling V2 or accepting credentials", () => {
  expect(readConnection(JSON.stringify(connection), state)).toEqual({
    policyId: "17",
    applicationPrincipalId: "123",
    issuer: state.issuer,
    audience: state.audience,
  });
  expect(() =>
    readConnection(
      JSON.stringify({ ...connection, serviceKey: "secret" }),
      state,
    ),
  ).toThrow();
});
it("rejects files from a different platform installation, workspace or AKB origin", () => {
  for (const key of ["platformId", "installationId", "workspaceId", "resource"])
    expect(() =>
      readConnection(JSON.stringify({ ...connection, [key]: "other" }), state),
    ).toThrow("mismatch");
  for (const bad of [
    { ...connection, policyId: "0" },
    { ...connection, issuer: "http://issuer.example" },
    { ...connection, format: "akb-agent-key-registration/v1" },
  ])
    expect(() => readConnection(JSON.stringify(bad), state)).toThrow();
});
