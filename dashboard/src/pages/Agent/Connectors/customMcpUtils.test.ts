import { describe, expect, it } from "vitest";

import { cardsToServers, newAkbCard } from "./customMcpUtils";

describe("newAkbCard", () => {
  it("creates a streamable HTTP AKB draft without a secret or implicit default access", () => {
    const card = newAkbCard(0, []);

    expect(card).toMatchObject({
      name: "akb",
      displayName: "AKB 知识库",
      transport: "streamable_http",
      url: "",
      headersText: "",
      enabled: true,
      defaultOpen: false,
      shared: false,
      collapsed: false,
    });
    expect(cardsToServers([card]).akb).toMatchObject({
      transport: "streamable_http",
      url: "",
      enabled: true,
    });
    expect(cardsToServers([card]).akb.headers).toBeUndefined();
  });

  it("keeps AKB server IDs unique", () => {
    expect(newAkbCard(1, ["akb", "akb-2"]).name).toBe("akb-3");
  });
});
