import { expect, it } from "vitest";
import type { TFunction } from "i18next";
import { checkHint } from "./diagnostics";
const t = ((key: string) => key) as TFunction;
it("shows success for successful checks with an empty error code", () => {
  expect(checkHint("", t, "success")).toBe(
    "externalKnowledge.diagnostics.passed",
  );
});
it("explains missing credentials, invalid KB configuration and protocol errors separately", () => {
  expect(checkHint("INVALID_CREDENTIALS", t)).toBe(
    "externalKnowledge.diagnostics.credentials",
  );
  expect(checkHint("INVALID_ARGUMENT", t)).toBe(
    "externalKnowledge.diagnostics.configuration",
  );
  expect(checkHint("INVALID_JSON", t)).toBe(
    "externalKnowledge.diagnostics.protocol",
  );
});
