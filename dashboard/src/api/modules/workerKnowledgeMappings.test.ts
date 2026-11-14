import { expect, it, vi } from "vitest";
import { request } from "../request";
import { workerKnowledgeMappingsApi as api } from "./workerKnowledgeMappings";
vi.mock("../request", () => ({ request: vi.fn() }));
it("encodes all resource identifiers and sends exact revision for revocation", async () => {
  await api.list("o/a", "w/b", 10, 10);
  expect(request).toHaveBeenLastCalledWith(
    "/organizations/o%2Fa/digital-workers/w%2Fb/knowledge-mappings?limit=10&offset=10",
  );
  await api.revoke("o/a", "w/b", "m/c", 7);
  expect(request).toHaveBeenLastCalledWith(
    "/organizations/o%2Fa/digital-workers/w%2Fb/knowledge-mappings/m%2Fc/revoke",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_revision: 7 }),
    },
  );
});
