import { describe, expect, it } from "vitest";
import { parseApiError } from "./apiError";
describe("structured HTTP errors", () => {
  it("reads the existing detail object envelope", () => {
    expect(
      parseApiError(
        new Error('403 {"detail":{"code":"FORBIDDEN","message":"Denied"}}'),
      ),
    ).toEqual({ code: "FORBIDDEN", message: "Denied" });
  });
  it("keeps standard error envelope precedence", () => {
    expect(
      parseApiError(
        new Error(
          '{"error":{"code":"PRIMARY","message":"Primary"},"detail":{"code":"SECONDARY","message":"Other"}}',
        ),
      ),
    ).toMatchObject({ code: "PRIMARY", message: "Primary" });
  });
  it("does not treat validation arrays as a detail message", () => {
    expect(
      parseApiError(new Error('{"detail":[{"msg":"bad input"}]}')),
    ).toBeNull();
  });
});
