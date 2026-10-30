// @vitest-environment node
// jsdom has no WebCrypto, and vite strips node:crypto out of browser-like
// environments, so this file runs under Node where crypto.subtle is real.
import { createHash, createPrivateKey, createPublicKey } from "node:crypto";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import {
  archiveName,
  canGenerate,
  generateAgentKeyPair,
  isAgentId,
  keyPairArchive,
  randomAgentId,
  type AgentKeyPair,
} from "./agentKeygen";

const ID = "0f9c2b7e-5a41-4c8d-9f30-6b1d7e2a4c55";

describe("agentKeygen", () => {
  let pair: AgentKeyPair;

  beforeAll(async () => {
    pair = await generateAgentKeyPair(ID);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("randomizes and accepts lowercase UUIDs only", () => {
    expect(isAgentId(randomAgentId())).toBe(true);
    expect(isAgentId(ID)).toBe(true);
    expect(isAgentId("not-a-uuid")).toBe(false);
    expect(isAgentId("")).toBe(false);
  });

  it("writes a PKCS#8 RSA-3072 private key", () => {
    expect(pair.privatePem.startsWith("-----BEGIN PRIVATE KEY-----\n")).toBe(
      true,
    );
    expect(pair.privatePem.endsWith("-----END PRIVATE KEY-----\n")).toBe(true);
    const key = createPrivateKey(pair.privatePem);
    expect(key.asymmetricKeyType).toBe("rsa");
    expect(key.asymmetricKeyDetails?.modulusLength).toBe(3072);
  });

  it("publishes the matching public key as a JWK", () => {
    const jwk = JSON.parse(pair.publicJwk);
    expect(jwk).toMatchObject({ kty: "RSA", alg: "RS256", use: "sig" });
    const published = createPublicKey(createPrivateKey(pair.privatePem)).export(
      { format: "jwk" },
    );
    expect(jwk.n).toBe(published.n);
    expect(jwk.e).toBe(published.e);
  });

  // tools/agent-keygen and AKB both derive the kid this way; a mismatch would
  // make keys generated in the browser unusable everywhere else.
  it("derives the kid exactly like tools/agent-keygen", () => {
    const jwk = JSON.parse(pair.publicJwk);
    const expected = createHash("sha256")
      .update(JSON.stringify({ e: jwk.e, kty: "RSA", n: jwk.n }))
      .digest("base64url");
    expect(jwk.kid).toBe(expected);
    expect(pair.kid).toBe(expected);
  });

  it("describes itself with the offline generator's identity format", () => {
    expect(JSON.parse(pair.identityJson)).toEqual({
      format: "akb-agent-key/v1",
      agentId: ID,
      kid: pair.kid,
    });
  });

  it("packs the three artifacts into a zip", async () => {
    const { default: JSZip } = await import("jszip");
    const archive = await keyPairArchive(pair);
    expect(archive.size).toBeGreaterThan(0);
    // jszip reads Blob through FileReader, which Node lacks.
    const zip = await JSZip.loadAsync(await archive.arrayBuffer());
    expect(Object.keys(zip.files).sort()).toEqual([
      "identity.json",
      "private.pem",
      "public.jwk",
    ]);
    expect(await zip.file("private.pem")!.async("string")).toBe(
      pair.privatePem,
    );
    expect(archiveName(pair)).toBe(`agent-${ID}.zip`);
  });

  it("is available where WebCrypto exists", () => {
    expect(canGenerate()).toBe(true);
  });

  it("refuses an agent id that is not a UUID", async () => {
    await expect(generateAgentKeyPair("nope")).rejects.toThrow(/UUID/);
  });

  it("is unavailable where the browser has no WebCrypto", () => {
    vi.stubGlobal("crypto", undefined);
    expect(canGenerate()).toBe(false);
    vi.stubGlobal("crypto", { randomUUID: () => ID });
    expect(canGenerate()).toBe(false);
  });
});
