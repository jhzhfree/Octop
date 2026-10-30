import { App } from "antd";
import { beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  agentIdentityApi,
  type SourceIdentityState,
} from "@/api/modules/agentIdentity";
import type { ExternalKnowledgeSource } from "@/api/modules/externalKnowledge";
import AgentIdentityDrawer from "./AgentIdentityDrawer";
vi.mock("react-i18next", () => {
  const t = (key: string) => key;
  return { useTranslation: () => ({ t }) };
});
vi.mock("@/api/modules/agentIdentity", () => ({
  agentIdentityApi: {
    state: vi.fn(),
    configure: vi.fn(),
    prepare: vi.fn(),
    prove: vi.fn(),
    activate: vi.fn(),
    probe: vi.fn(),
    search: vi.fn(),
  },
}));
vi.mock("@/api/modules/externalKnowledge", () => ({
  externalKnowledgeApi: { update: vi.fn() },
}));
const prefix = "externalKnowledge.identity.";
const publicKey = {
  kid: "k".repeat(43),
  publicJwk: {
    kty: "RSA",
    n: "n".repeat(512),
    e: "AQAB",
    kid: "k".repeat(43),
    alg: "RS256",
    use: "sig",
  },
  owner: {
    platformId: "octop",
    installationId: "install",
    workspaceId: "source",
    externalAgentId: "e21cce9c-1b16-4ab8-a76a-bb761df919b8",
  },
  localState: "PENDING_REGISTRATION",
  createdAt: "2026-10-30",
};
const source = {
  source_id: "source",
  kind: "akb-rest",
  endpoint: "https://akb.example",
} as ExternalKnowledgeSource;
function state(ready = true): SourceIdentityState {
  return {
    mode: "LEGACY",
    policyId: "17",
    applicationPrincipalId: "123",
    issuer: "https://octop.local/agents",
    audience: "akb",
    platformId: "octop",
    installationId: "install",
    workspaceId: "source",
    resource: "https://akb.example",
    runtimeReady: ready,
    sdkVersion: "0.3.0",
    agents: [
      {
        agentId: "alpha",
        name: "Alpha",
        externalAgentId: publicKey.owner.externalAgentId,
        bindingId: "",
        activeKid: null,
        keys: [publicKey],
      },
      {
        agentId: "beta",
        name: "Beta",
        externalAgentId: "",
        bindingId: "",
        activeKid: null,
        keys: [],
      },
    ],
  };
}
async function chooseAgent(name: string) {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: prefix + "agent" }));
  fireEvent.click(
    await screen.findByText(name, {
      selector: ".ant-select-item-option-content",
    }),
  );
}
beforeEach(() => vi.clearAllMocks());
it("does not claim a prepared key is verified and shows a rejected remote activation", async () => {
  vi.mocked(agentIdentityApi.state).mockResolvedValue(state());
  vi.mocked(agentIdentityApi.activate).mockRejectedValue(
    new Error("AKB registration required"),
  );
  render(
    <App>
      <AgentIdentityDrawer
        source={source}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />
    </App>,
  );
  await screen.findByText(prefix + "agentsTitle");
  await chooseAgent("Alpha (alpha)");
  expect(screen.queryByText(prefix + "verified")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: prefix + "probe" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: prefix + "activate" }));
  await screen.findByText("AKB registration required");
  expect(agentIdentityApi.activate).toHaveBeenCalledWith(
    "source",
    "alpha",
    publicKey.kid,
  );
  expect(screen.queryByText(prefix + "verified")).not.toBeInTheDocument();
});
it("disables key generation when the SDK is unavailable", async () => {
  vi.mocked(agentIdentityApi.state).mockResolvedValue(state(false));
  render(
    <App>
      <AgentIdentityDrawer
        source={source}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />
    </App>,
  );
  await screen.findByText(prefix + "runtimeUnavailable");
  await chooseAgent("Beta (beta)");
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: prefix + "prepare" }),
    ).toBeDisabled(),
  );
  expect(agentIdentityApi.prepare).not.toHaveBeenCalled();
});
