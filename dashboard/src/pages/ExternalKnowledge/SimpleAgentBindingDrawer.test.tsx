import { App } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { ExternalKnowledgeSource } from "@/api/modules/externalKnowledge";
import SimpleAgentBindingDrawer from "./SimpleAgentBindingDrawer";

const api = vi.hoisted(() => ({
  state: vi.fn(),
  generate: vi.fn(),
  connect: vi.fn(),
  search: vi.fn(),
  disconnect: vi.fn(),
  discard: vi.fn(),
}));
const download = vi.hoisted(() => vi.fn());
vi.mock("@/api/modules/agentIdentity", () => ({ agentIdentityApi: api }));
vi.mock("@/utils/saveBlobAsFile", () => ({ saveBlobAsFile: download }));
vi.mock("@/hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));
vi.mock("react-i18next", () => {
  const t = (key: string) => key;
  return { useTranslation: () => ({ t }) };
});
const externalId = "e21cce9c-1b16-4ab8-a76a-bb761df919b8";
const source = {
  source_id: "source-a",
  display_name: "AKB",
} as ExternalKnowledgeSource;
beforeEach(() => {
  vi.clearAllMocks();
  api.state.mockResolvedValue({
    revision: 4,
    runtimeReady: true,
    agents: [
      { agentId: "alpha", name: "Alpha", externalAgentId: "", activeKid: null },
    ],
  });
  api.generate.mockResolvedValue({
    externalAgentId: externalId,
    kid: "public-kid",
    publicJwk: { kty: "RSA", kid: "public-kid" },
    keyReference: "01965897-0976-471c-8d64-27eb682a227b",
  });
  api.connect.mockResolvedValue({ activeKid: "public-kid" });
  download.mockResolvedValue(undefined);
});
it("binds a host-generated reference without requiring or returning a browser private key", async () => {
  render(
    <App>
      <SimpleAgentBindingDrawer
        source={source}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />
    </App>,
  );
  await waitFor(() => expect(api.state).toHaveBeenCalled());
  await waitFor(() =>
    expect(
      screen.getByText("externalKnowledge.simple.generate").closest("button"),
    ).not.toBeDisabled(),
  );
  fireEvent.mouseDown(
    screen.getByRole("combobox", {
      name: "externalKnowledge.simple.systemAgent",
    }),
  );
  fireEvent.click(await screen.findByText("Alpha · alpha"));
  fireEvent.click(screen.getByText("externalKnowledge.simple.generate"));
  await screen.findByText("public-kid");
  expect(api.generate).toHaveBeenCalledWith("source-a", expect.any(String));
  expect(download).toHaveBeenCalledWith(
    expect.any(Blob),
    `${externalId}-public.jwk`,
  );
  fireEvent.click(screen.getByText("externalKnowledge.simple.saveAndTest"));
  await waitFor(() =>
    expect(api.connect).toHaveBeenCalledWith(
      "source-a",
      "alpha",
      externalId,
      "",
      "01965897-0976-471c-8d64-27eb682a227b",
      4,
    ),
  );
});

it("offers an explicit replacement import and read-only public material for a configured identity", async () => {
  api.state.mockResolvedValue({
    runtimeReady: true,
    revision: 5,
    resourceCount: 2,
    agents: [
      {
        agentId: "alpha",
        name: "Alpha",
        externalAgentId: externalId,
        activeKid: "old",
        keySaved: true,
        publicJwk: { kty: "RSA", kid: "old" },
      },
    ],
  });
  render(
    <App>
      <SimpleAgentBindingDrawer
        source={source}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />
    </App>,
  );
  fireEvent.click(await screen.findByText("externalKnowledge.simple.select"));
  expect(
    await screen.findByText("externalKnowledge.simple.replaceKey"),
  ).toBeEnabled();
  fireEvent.click(screen.getByText("externalKnowledge.simple.replaceKey"));
  expect(
    await screen.findByText("externalKnowledge.simple.importPrivate"),
  ).toBeVisible();
  expect(
    screen.queryByText("externalKnowledge.simple.importPublic"),
  ).not.toBeInTheDocument();
});

it("removes a connection identity with the displayed configuration revision", async () => {
  api.state.mockResolvedValue({
    runtimeReady: true,
    revision: 7,
    resourceCount: 2,
    agents: [
      {
        agentId: "alpha",
        name: "Alpha",
        externalAgentId: externalId,
        activeKid: "old",
      },
    ],
  });
  api.disconnect.mockResolvedValue({ ok: true });
  render(
    <App>
      <SimpleAgentBindingDrawer
        source={source}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />
    </App>,
  );
  fireEvent.click(await screen.findByText("externalKnowledge.simple.unbind"));
  fireEvent.click(await screen.findByText("OK"));
  await waitFor(() =>
    expect(api.disconnect).toHaveBeenCalledWith("source-a", "alpha", 7),
  );
});
