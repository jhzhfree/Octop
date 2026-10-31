import { App } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import ExternalKnowledge from "./index";

const api = vi.hoisted(() => ({
  list: vi.fn(),
  kinds: vi.fn(),
  connections: vi.fn(),
  createConnection: vi.fn(),
  updateConnection: vi.fn(),
}));
vi.mock("@/api/modules/externalKnowledge", () => ({
  externalKnowledgeApi: api,
}));
vi.mock("@/context/AgentContext", () => ({ useAgent: () => ({ agents: [] }) }));
vi.mock("@/hooks/useIsMobile", () => ({ useIsMobile: () => false }));
vi.mock("@/layouts/PageShell", () => ({
  default: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}));
vi.mock("@/hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));
vi.mock("react-i18next", () => {
  const t = (key: string) => key;
  return { useTranslation: () => ({ t }) };
});

const connection = {
  connection_id: "c",
  display_name: "Company KB",
  endpoint: "https://kb.example",
  kind: "akb-mcp",
  auth_type: "service",
  revision: 4,
  resource_count: 0,
  agent_count: 0,
  enabled: true,
  shared: false,
  has_credentials: true,
  can_manage: true,
};
beforeEach(() => {
  vi.clearAllMocks();
  api.list.mockResolvedValue([]);
  api.kinds.mockResolvedValue({ kinds: ["akb-mcp", "akb-rest"] });
  api.connections.mockResolvedValue([connection]);
  api.createConnection.mockResolvedValue(connection);
  api.updateConnection.mockResolvedValue({ ...connection, enabled: false });
});

it("creates a service connection independently of any knowledge resource", async () => {
  render(
    <App>
      <ExternalKnowledge />
    </App>,
  );
  await screen.findByText("Company KB");
  fireEvent.click(screen.getByText("externalKnowledge.newConnection"));
  fireEvent.change(screen.getByLabelText("externalKnowledge.name"), {
    target: { value: "New Connection" },
  });
  fireEvent.change(screen.getByLabelText("externalKnowledge.endpoint"), {
    target: { value: "https://new.example" },
  });
  fireEvent.change(screen.getByLabelText("externalKnowledge.apiKey"), {
    target: { value: "synthetic-service-key" },
  });
  fireEvent.click(screen.getByText("externalKnowledge.save"));
  await waitFor(() =>
    expect(api.createConnection).toHaveBeenCalledWith(
      expect.objectContaining({
        display_name: "New Connection",
        endpoint: "https://new.example",
        auth_type: "service",
        api_key: "synthetic-service-key",
      }),
    ),
  );
  expect(api.list).toHaveBeenCalledTimes(2);
});

it("disables the connection using its revision instead of editing every resource", async () => {
  render(
    <App>
      <ExternalKnowledge />
    </App>,
  );
  await screen.findByText("Company KB");
  fireEvent.click(screen.getByRole("switch"));
  await waitFor(() =>
    expect(api.updateConnection).toHaveBeenCalledWith("c", {
      revision: 4,
      enabled: false,
    }),
  );
});
