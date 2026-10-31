import { describe, expect, it } from "vitest";
import { isAgentId, randomAgentId } from "./agentKeygen";
describe("Agent identifiers", () => {
  it("accepts canonical UUIDs only", () => {
    expect(isAgentId("e21cce9c-1b16-4ab8-a76a-bb761df919b8")).toBe(true);
    expect(isAgentId("E21CCE9C-1B16-4AB8-A76A-BB761DF919B8")).toBe(false);
    expect(isAgentId("../private.pem")).toBe(false);
  });
  it("generates an identifier without key material", () => {
    expect(isAgentId(randomAgentId())).toBe(true);
  });
});
