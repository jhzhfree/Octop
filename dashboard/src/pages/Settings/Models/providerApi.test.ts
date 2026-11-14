import { describe, expect, it, vi } from "vitest";
import { request } from "../../../api/request";
import { fetchProviderModels, testProviderDraft } from "./providerApi";

vi.mock("../../../api/request", () => ({
  request: vi.fn().mockResolvedValue({ ok: true }),
}));

describe("server-side provider credentials", () => {
  it("fetches with a provider reference instead of returning a stored key to the client", async () => {
    await fetchProviderModels({
      kind: "openai",
      provider_id: 7,
      base_url: "https://gateway.example/v1",
    });
    const call = vi.mocked(request).mock.calls.at(-1)!;
    expect(JSON.parse(call[1]!.body as string)).toMatchObject({
      provider_id: 7,
      api_key: null,
    });
  });
  it("keeps a supplied replacement key in draft tests", async () => {
    await testProviderDraft({
      name: "provider",
      kind: "openai",
      provider_id: 7,
      model_id: "m",
      api_key: "synthetic-replacement",
    });
    const call = vi.mocked(request).mock.calls.at(-1)!;
    expect(JSON.parse(call[1]!.body as string)).toMatchObject({
      provider_id: 7,
      api_key: "synthetic-replacement",
    });
  });
});
