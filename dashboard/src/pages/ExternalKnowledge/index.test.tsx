import { App } from "antd";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import ExternalKnowledge from "./index";

const api = vi.hoisted(() => ({
  list: vi.fn(),
  kinds: vi.fn(),
  connections: vi.fn(),
  createConnection: vi.fn(),
  updateConnection: vi.fn(),
  update: vi.fn(),
  probeConnection: vi.fn(),
  probe: vi.fn(),
  connectionRefs: vi.fn(),
}));
vi.mock("@/api/modules/externalKnowledge", () => ({
  externalKnowledgeApi: api,
}));
const identity = vi.hoisted(() => ({ state: vi.fn(), bases: vi.fn() }));
vi.mock("@/api/modules/agentIdentity", () => ({ agentIdentityApi: identity }));
vi.mock("@/context/AgentContext", () => ({ useAgent: () => ({ agents: [] }) }));
vi.mock("@/hooks/useIsMobile", () => ({ useIsMobile: () => false }));
vi.mock("@/layouts/PageShell", () => ({
  default: ({
    children,
    actions,
  }: {
    children: React.ReactNode;
    actions: React.ReactNode;
  }) => (
    <div>
      {actions}
      {children}
    </div>
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
  api.probeConnection.mockResolvedValue({
    ok: false,
    code: "INVALID_CREDENTIALS",
    stage: "connection",
  });
  api.connectionRefs.mockResolvedValue({
    refs: [{ ref: "KB_FIRST", name: "First" }],
  });
  api.probe.mockResolvedValue({
    ok: true,
    message: "",
    tools: ["knowledge_search"],
    tool_schemas: [
      {
        name: "knowledge_search",
        description: "Search",
        input_schema: {
          properties: {
            knowledgeBaseCode: { type: "string" },
            tag: { type: "string" },
          },
          required: ["knowledgeBaseCode"],
        },
      },
    ],
  });
  api.updateConnection.mockResolvedValue({ ...connection, enabled: false });
  identity.state.mockResolvedValue({ runtimeReady: true, agents: [] });
  identity.bases.mockResolvedValue({ refs: [] });
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

it("shows a legacy connection as requiring attention rather than service authentication", async () => {
  api.connections.mockResolvedValue([
    { ...connection, auth_type: "legacy", needs_attention: true },
  ]);
  render(
    <App>
      <ExternalKnowledge />
    </App>,
  );
  expect(
    await screen.findByText("externalKnowledge.needsAttention"),
  ).toBeVisible();
  expect(
    screen.queryByText("externalKnowledge.authService"),
  ).not.toBeInTheDocument();
});

it("opens independent identity management before any resource exists", async () => {
  api.connections.mockResolvedValue([{ ...connection, auth_type: "agent" }]);
  render(
    <App>
      <ExternalKnowledge />
    </App>,
  );
  fireEvent.click(await screen.findByText("externalKnowledge.details"));
  fireEvent.click(
    await screen.findByRole("tab", {
      name: "externalKnowledge.identity.manage",
    }),
  );
  expect(
    await screen.findByRole("button", {
      name: "externalKnowledge.identity.manage",
    }),
  ).toBeEnabled();
});

const resource = {
  source_id: "resource",
  connection_id: "c",
  display_name: "Policies",
  kind: "akb-mcp",
  endpoint: "https://kb.example",
  auth_type: "agent",
  revision: 4,
  resource_revision: 2,
  ref: "KB_HR",
  top_k: 8,
  min_score: 0,
  timeout_ms: 10000,
  tool_name: "knowledge_search",
  tool_arguments: {},
  selector_argument: "",
  shared: false,
  enabled: true,
  bindings: [],
  can_manage: true,
  can_manage_connection: true,
};

it("loads Agent choices before the first catalog request", async () => {
  // The catalog dropdown travels with the knowledge base field, which MCP
  // resources no longer show, so this stays exercised on a REST resource.
  api.list.mockResolvedValue([{ ...resource, kind: "akb-rest" }]);
  identity.state.mockResolvedValue({
    runtimeReady: true,
    agents: [
      {
        agentId: "alpha",
        name: "Alpha",
        externalAgentId: "uuid",
        activeKid: "kid",
      },
    ],
  });
  identity.bases.mockResolvedValue({ refs: [{ ref: "KB_HR", name: "HR" }] });
  render(
    <App>
      <ExternalKnowledge />
    </App>,
  );
  await screen.findByText("Company KB");
  fireEvent.click(
    screen.getByRole("tab", { name: "externalKnowledge.resources (1)" }),
  );
  const row = (await screen.findByText("Policies")).closest("tr")!;
  fireEvent.click(
    within(row).getByRole("button", { name: "externalKnowledge.edit" }),
  );
  await waitFor(() => expect(identity.state).toHaveBeenCalledWith("resource"));
  const catalog = await screen.findByRole("combobox", {
    name: "externalKnowledge.catalogAgent",
  });
  expect(
    screen.getByRole("button", { name: "externalKnowledge.fetchRefs" }),
  ).toBeDisabled();
  fireEvent.mouseDown(catalog);
  fireEvent.click(await screen.findByText("Alpha"));
  fireEvent.click(
    screen.getByRole("button", { name: "externalKnowledge.fetchRefs" }),
  );
  await waitFor(() =>
    expect(identity.bases).toHaveBeenCalledWith("resource", "alpha"),
  );
});

it("sends both versions when saving a resource without rewriting connection credentials", async () => {
  api.list.mockResolvedValue([{ ...resource, auth_type: "service" }]);
  api.update.mockResolvedValue(resource);
  render(
    <App>
      <ExternalKnowledge />
    </App>,
  );
  await screen.findByText("Company KB");
  fireEvent.click(
    screen.getByRole("tab", { name: "externalKnowledge.resources (1)" }),
  );
  const row = (await screen.findByText("Policies")).closest("tr")!;
  fireEvent.click(
    within(row).getByRole("button", { name: "externalKnowledge.edit" }),
  );
  fireEvent.change(await screen.findByLabelText("externalKnowledge.name"), {
    target: { value: "Updated Policies" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "externalKnowledge.save" }),
  );
  await waitFor(() =>
    expect(api.update).toHaveBeenCalledWith(
      "resource",
      expect.objectContaining({
        revision: 4,
        resource_revision: 2,
        display_name: "Updated Policies",
      }),
    ),
  );
  const body = api.update.mock.calls[0][1];
  expect(body).not.toHaveProperty("auth_type");
  expect(body).not.toHaveProperty("api_key");
  expect(body).not.toHaveProperty("endpoint");
});

it("requires the service Key before saving a new AKB connection", async () => {
  render(
    <App>
      <ExternalKnowledge />
    </App>,
  );
  await screen.findByText("Company KB");
  fireEvent.click(
    screen.getByRole("button", { name: "externalKnowledge.newConnection" }),
  );
  fireEvent.change(screen.getByLabelText("externalKnowledge.name"), {
    target: { value: "Missing Key" },
  });
  fireEvent.change(screen.getByLabelText("externalKnowledge.endpoint"), {
    target: { value: "https://kb.example" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "externalKnowledge.save" }),
  );
  expect(
    await screen.findByText("externalKnowledge.missingServiceKey"),
  ).toBeVisible();
  expect(api.createConnection).not.toHaveBeenCalled();
});

it("checks a connection without creating or searching a knowledge resource", async () => {
  render(
    <App>
      <ExternalKnowledge />
    </App>,
  );
  await screen.findByText("Company KB");
  fireEvent.click(
    screen.getByRole("button", { name: "externalKnowledge.checkConnection" }),
  );
  await waitFor(() => expect(api.probeConnection).toHaveBeenCalledWith("c"));
  expect(api.update).not.toHaveBeenCalled();
  expect(identity.bases).not.toHaveBeenCalled();
});

it("offers the tool list instead of a knowledge base field for a new MCP resource", async () => {
  render(
    <App>
      <ExternalKnowledge />
    </App>,
  );
  await screen.findByText("Company KB");
  fireEvent.click(
    screen.getByRole("tab", { name: "externalKnowledge.resources (0)" }),
  );
  fireEvent.click(
    screen.getAllByRole("button", { name: "externalKnowledge.new" })[0],
  );
  expect(
    await screen.findByRole("button", {
      name: "externalKnowledge.fetchTools",
    }),
  ).toBeTruthy();
  expect(screen.queryByLabelText("externalKnowledge.ref")).toBeNull();
  expect(api.createConnection).not.toHaveBeenCalled();
});

it("keeps the knowledge base field for REST resources", async () => {
  api.list.mockResolvedValue([{ ...resource, kind: "akb-rest" }]);
  render(
    <App>
      <ExternalKnowledge />
    </App>,
  );
  await screen.findByText("Company KB");
  fireEvent.click(
    screen.getByRole("tab", { name: "externalKnowledge.resources (1)" }),
  );
  const row = (await screen.findByText("Policies")).closest("tr")!;
  fireEvent.click(
    within(row).getByRole("button", { name: "externalKnowledge.edit" }),
  );
  expect(
    (await screen.findAllByLabelText("externalKnowledge.ref")).length,
  ).toBeGreaterThan(0);
  expect(
    screen.queryByRole("button", { name: "externalKnowledge.fetchTools" }),
  ).toBeNull();
});

it("loads the tool list into editable arguments, including the stored base", async () => {
  api.list.mockResolvedValue([resource]);
  render(
    <App>
      <ExternalKnowledge />
    </App>,
  );
  await screen.findByText("Company KB");
  fireEvent.click(
    screen.getByRole("tab", { name: "externalKnowledge.resources (1)" }),
  );
  const row = (await screen.findByText("Policies")).closest("tr")!;
  fireEvent.click(
    within(row).getByRole("button", { name: "externalKnowledge.edit" }),
  );
  // The saved base comes back as the argument a tool would take.
  expect(await screen.findByDisplayValue("knowledgeBaseCode")).toBeTruthy();
  fireEvent.click(
    screen.getByRole("button", { name: "externalKnowledge.fetchTools" }),
  );
  await waitFor(() => expect(api.probe).toHaveBeenCalledWith("resource"));
  // What the tool declares joins the rows the operator can fill in.
  await waitFor(() =>
    expect(
      screen.getAllByPlaceholderText("externalKnowledge.toolArgumentKey"),
    ).toHaveLength(2),
  );
  expect(screen.getByDisplayValue("tag")).toBeTruthy();
  expect(screen.getByDisplayValue("KB_HR")).toBeTruthy();
});

it("saves the base from the tool arguments as the source identifier", async () => {
  api.list.mockResolvedValue([resource]);
  api.update.mockResolvedValue(resource);
  render(
    <App>
      <ExternalKnowledge />
    </App>,
  );
  await screen.findByText("Company KB");
  fireEvent.click(
    screen.getByRole("tab", { name: "externalKnowledge.resources (1)" }),
  );
  const row = (await screen.findByText("Policies")).closest("tr")!;
  fireEvent.click(
    within(row).getByRole("button", { name: "externalKnowledge.edit" }),
  );
  const values = await screen.findAllByPlaceholderText(
    "externalKnowledge.toolArgumentValue",
  );
  fireEvent.change(values[0], { target: { value: "KB_NEW" } });
  fireEvent.click(
    screen.getByRole("button", { name: "externalKnowledge.save" }),
  );
  await waitFor(() =>
    expect(api.update).toHaveBeenCalledWith(
      "resource",
      expect.objectContaining({
        ref: "KB_NEW",
        selector_argument: "knowledgeBaseCode",
      }),
    ),
  );
  const body = api.update.mock.calls[0][1];
  expect(body.tool_arguments).not.toHaveProperty("knowledgeBaseCode");
});

it("lists the fetched tools and keeps the probe detail behind an icon", async () => {
  api.list.mockResolvedValue([resource]);
  api.probe.mockResolvedValue({
    ok: true,
    message: "reachable",
    tools: ["knowledge_search"],
    protocol_version: "2024-11-05",
    tool_schemas: [
      {
        name: "knowledge_search",
        description: "Search",
        input_schema: {
          properties: { knowledgeBaseCode: { type: "string" } },
          required: ["knowledgeBaseCode"],
        },
      },
    ],
  });
  render(
    <App>
      <ExternalKnowledge />
    </App>,
  );
  await screen.findByText("Company KB");
  fireEvent.click(
    screen.getByRole("tab", { name: "externalKnowledge.resources (1)" }),
  );
  const row = (await screen.findByText("Policies")).closest("tr")!;
  fireEvent.click(
    within(row).getByRole("button", { name: "externalKnowledge.edit" }),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "externalKnowledge.fetchTools" }),
  );
  await waitFor(() => expect(api.probe).toHaveBeenCalledWith("resource"));
  // Tools come back as a pickable list, not hidden inside a dropdown.
  const tool = await screen.findByRole("button", {
    name: "knowledge_search — Search",
  });
  fireEvent.click(tool);
  // The detail the probe returned stays out of the form until asked for.
  expect(screen.queryByText("reachable")).toBeNull();
  fireEvent.click(
    screen.getByRole("button", { name: "externalKnowledge.probeDetails" }),
  );
  await waitFor(() => expect(screen.getByText("reachable")).toBeTruthy());
  expect(screen.getByText("2024-11-05")).toBeTruthy();
});
