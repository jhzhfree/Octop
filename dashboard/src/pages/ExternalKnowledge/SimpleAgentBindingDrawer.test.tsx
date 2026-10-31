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
  fireEvent.mouseDown(screen.getByRole("combobox"));
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
    ),
  );
});
