import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import KnowledgeMappings from "./KnowledgeMappings";
import type { DigitalWorker } from "../../api/modules/organizations";
import {
  mappingCapabilities,
  mappingSourceOptions,
} from "./knowledgeMappingOptions";
import type { ExternalKnowledgeSource } from "../../api/modules/externalKnowledge";
const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  revoke: vi.fn(),
  inspect: vi.fn(),
  sources: vi.fn(),
}));
vi.mock("../../api/modules/workerKnowledgeMappings", () => ({
  workerKnowledgeMappingsApi: mocks,
}));
vi.mock("../../api/modules/externalKnowledge", () => ({
  externalKnowledgeApi: { list: mocks.sources },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const worker = {
  worker_id: "w",
  agent_id: "a",
  status: "active",
  version: 1,
  capability_version: 2,
  capabilities: {
    knowledge_sources: [
      { provider: "akb", mcp_server: "m", knowledge_base_ref: "kb" },
    ],
  },
} as unknown as DigitalWorker;
const source = {
  source_id: "s",
  display_name: "Source",
  kind: "akb-rest",
  auth_type: "agent",
  enabled: true,
  connection_enabled: true,
  revision: 3,
  resource_revision: 4,
  ref: "kb",
  bindings: [{ target_type: "agent", target_id: "a" }],
} as ExternalKnowledgeSource;
const row = {
  mapping_id: "map",
  external_source_id: "s",
  status: "draft",
  revision: 7,
  local_snapshot_current: null,
};
async function open() {
  fireEvent.click(screen.getByText("knowledgeMappings.title"));
  await screen.findByText("map");
}
async function select() {
  fireEvent.change(screen.getByLabelText("knowledgeMappings.capability"), {
    target: { value: "1" },
  });
  fireEvent.change(screen.getByLabelText("knowledgeMappings.source"), {
    target: { value: "s" },
  });
  fireEvent.click(screen.getByText("knowledgeMappings.create"));
}
beforeEach(() => {
  vi.clearAllMocks();
  mocks.list.mockResolvedValue({ items: [row], offset: 0, limit: 10 });
  mocks.sources.mockResolvedValue([source]);
  mocks.create.mockResolvedValue(row);
  mocks.revoke.mockResolvedValue({ ...row, status: "revoked" });
});
describe("knowledge mappings", () => {
  it("does not fetch for members", () => {
    render(
      <KnowledgeMappings
        organizationId="o"
        worker={worker}
        canManage={false}
      />,
    );
    expect(mocks.list).not.toHaveBeenCalled();
    expect(screen.queryByText("knowledgeMappings.title")).toBeNull();
  });
  it("creates only after confirmation with observed revisions", async () => {
    render(<KnowledgeMappings organizationId="o" worker={worker} canManage />);
    await open();
    expect(screen.getByText("knowledgeMappings.unchecked")).toBeTruthy();
    await select();
    expect(mocks.create).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("knowledgeMappings.confirm"));
    await waitFor(() =>
      expect(mocks.create).toHaveBeenCalledWith("o", "w", {
        mcp_server: "m",
        knowledge_base_ref: "kb",
        external_source_id: "s",
        expected_capability_version: 2,
        expected_connection_revision: 3,
        expected_resource_revision: 4,
      }),
    );
  });
  it("cancels confirmation without write and revokes with revision", async () => {
    render(<KnowledgeMappings organizationId="o" worker={worker} canManage />);
    await open();
    await select();
    fireEvent.click(screen.getByText("knowledgeMappings.cancel"));
    expect(mocks.create).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("knowledgeMappings.revoke"));
    expect(mocks.revoke).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("knowledgeMappings.confirm"));
    await waitFor(() =>
      expect(mocks.revoke).toHaveBeenCalledWith("o", "w", "map", 7),
    );
  });
  it("clears pending write after conflict and requires refresh", async () => {
    mocks.create.mockRejectedValue(
      new Error(
        '409 - {"detail":{"code":"DIGITAL_WORKER_CAPABILITY_VERSION_CONFLICT"}}',
      ),
    );
    render(<KnowledgeMappings organizationId="o" worker={worker} canManage />);
    await open();
    await select();
    fireEvent.click(screen.getByText("knowledgeMappings.confirm"));
    await screen.findByText("knowledgeMappings.conflict");
    expect(screen.queryByText("knowledgeMappings.create")).toBeNull();
    expect(mocks.create).toHaveBeenCalledTimes(1);
  });
  it("ignores an old response after changing worker", async () => {
    let finish!: (v: unknown) => void;
    mocks.list.mockImplementationOnce(
      () =>
        new Promise((r) => {
          finish = r;
        }),
    );
    const { rerender } = render(
      <KnowledgeMappings organizationId="o" worker={worker} canManage />,
    );
    fireEvent.click(screen.getByText("knowledgeMappings.title"));
    rerender(
      <KnowledgeMappings
        organizationId="o"
        worker={{ ...worker, worker_id: "other" }}
        canManage
      />,
    );
    finish({ items: [row], limit: 10, offset: 0 });
    await waitFor(() => expect(screen.queryByText("map")).toBeNull());
  });
  it("checks one row and uses bounded next-page offset", async () => {
    mocks.list
      .mockResolvedValueOnce({
        items: Array.from({ length: 10 }, (_, i) => ({
          ...row,
          mapping_id: `map${i}`,
        })),
        offset: 0,
        limit: 10,
      })
      .mockResolvedValue({ items: [], offset: 10, limit: 10 });
    mocks.inspect.mockResolvedValue({
      ...row,
      mapping_id: "map0",
      local_snapshot_current: true,
    });
    render(<KnowledgeMappings organizationId="o" worker={worker} canManage />);
    fireEvent.click(screen.getByText("knowledgeMappings.title"));
    await screen.findByText("map0");
    fireEvent.click(screen.getAllByText("knowledgeMappings.inspect")[0]);
    await screen.findByText("knowledgeMappings.current");
    expect(mocks.inspect).toHaveBeenCalledWith("o", "w", "map0");
    fireEvent.click(screen.getByText("knowledgeMappings.next"));
    await screen.findByText("knowledgeMappings.empty");
    expect(mocks.list).toHaveBeenLastCalledWith("o", "w", 10);
  });
  it("does not submit twice while a change is pending", async () => {
    let resolve!: (value: unknown) => void;
    mocks.create.mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    render(<KnowledgeMappings organizationId="o" worker={worker} canManage />);
    await open();
    await select();
    const button = screen.getByText("knowledgeMappings.confirm");
    fireEvent.click(button);
    fireEvent.click(button);
    expect(mocks.create).toHaveBeenCalledTimes(1);
    resolve(row);
    await waitFor(() => expect(mocks.list).toHaveBeenCalledTimes(2));
  });
  it("does not infer matches or accept missing revisions/binding", () => {
    expect(mappingCapabilities({ knowledge: ["kb"] })).toEqual([]);
    expect(mappingSourceOptions([source], "a", "other")).toEqual([]);
    expect(
      mappingSourceOptions(
        [
          { ...source, revision: undefined },
          { ...source, bindings: [] },
        ],
        "a",
        "kb",
      ),
    ).toEqual([]);
    expect(
      mappingCapabilities({
        akb_knowledge_sources: [
          { provider: "akb", mcp_server: "m", knowledge_base_code: "kb" },
        ],
      }),
    ).toEqual([{ mcp_server: "m", knowledge_base_ref: "kb" }]);
  });
});
