import { beforeEach, expect, it, vi } from "vitest";
import { request } from "../request";
import {
  mattermostPublicationApi,
  validPublicationCandidate,
} from "./mattermostPublication";
vi.mock("../request", () => ({ request: vi.fn() }));
beforeEach(() => vi.resetAllMocks());
const target = { organizationId: "org/a", runId: "run/b" };
const path = "/organizations/org%2Fa/scene-runs/run%2Fb/mattermost-publication";
it("uses encoded context and no-store for every read", () => {
  mattermostPublicationApi.get(target);
  expect(request).toHaveBeenLastCalledWith(path, { cache: "no-store" });
  mattermostPublicationApi.candidate(target);
  expect(request).toHaveBeenLastCalledWith(`${path}/candidate`, {
    cache: "no-store",
  });
});
it("sends only four server-derived expectations, never content, targets, credentials or actors", () => {
  const candidate = {
    expected_report_sha256: "a".repeat(64),
    expected_worker_version: 1,
    expected_intent_revision: 2,
    expected_configuration_revision: 3,
  };
  mattermostPublicationApi.create(target, {
    ...candidate,
    content: "do not send",
    token: "secret",
    proposed_by: 5,
  } as typeof candidate);
  expect(request).toHaveBeenLastCalledWith(path, {
    method: "POST",
    cache: "no-store",
    body: JSON.stringify(candidate),
  });
});
it.each(["approve", "revoke"] as const)(
  "%s transmits only the reviewed fingerprint",
  (decision) => {
    mattermostPublicationApi.decide(target, decision, "a".repeat(64));
    expect(request).toHaveBeenLastCalledWith(`${path}/${decision}`, {
      method: "POST",
      cache: "no-store",
      body: JSON.stringify({ expected_publication_sha256: "a".repeat(64) }),
    });
  },
);
it("rejects malformed candidates", () => {
  expect(
    validPublicationCandidate({
      expected_report_sha256: "a".repeat(64),
      expected_worker_version: 1,
      expected_intent_revision: 2,
      expected_configuration_revision: 3,
    }),
  ).toBe(true);
  expect(
    validPublicationCandidate({
      expected_report_sha256: "bad",
      expected_worker_version: 1,
      expected_intent_revision: 2,
      expected_configuration_revision: 3,
    }),
  ).toBe(false);
  expect(
    validPublicationCandidate({
      expected_report_sha256: "a".repeat(64),
      expected_worker_version: 1,
      expected_intent_revision: 2,
      expected_configuration_revision: 0,
    }),
  ).toBe(false);
});
