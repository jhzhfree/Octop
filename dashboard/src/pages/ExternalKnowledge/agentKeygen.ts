/**
 * Browser-side Agent key-pair generation.
 *
 * Produces exactly what tools/agent-keygen writes: RSA-3072, a PKCS#8 PEM
 * private key, a public JWK whose kid is the SHA-256 thumbprint of the
 * canonical JWK, and identity.json. Keys made here and keys made by the
 * offline generator are interchangeable, so a host without Node 22 can still
 * onboard from the dashboard.
 *
 * Nothing is uploaded while generating: the private key lives in memory until
 * the user submits the binding form.
 */

export interface AgentKeyPair {
  agentId: string;
  kid: string;
  privatePem: string;
  publicJwk: string;
  identityJson: string;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export function isAgentId(value: string): boolean {
  return UUID.test(value.trim().toLowerCase());
}

export function randomAgentId(): string {
  return crypto.randomUUID();
}

/** WebCrypto only exists in a secure context: https, localhost or file://. */
export function canGenerate(): boolean {
  return typeof crypto !== "undefined" && !!crypto.subtle?.generateKey;
}

function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function base64Url(bytes: Uint8Array): string {
  return toBase64(bytes)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function pem(label: string, der: ArrayBuffer): string {
  const body =
    toBase64(new Uint8Array(der))
      .match(/.{1,64}/g)
      ?.join("\n") ?? "";
  return `-----BEGIN ${label}-----\n${body}\n-----END ${label}-----\n`;
}

/** Same thumbprint as the offline generator: SHA-256 over {e, kty, n}. */
async function thumbprint(jwk: JsonWebKey): Promise<string> {
  const canonical = JSON.stringify({ e: jwk.e, kty: jwk.kty, n: jwk.n });
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(canonical),
  );
  return base64Url(new Uint8Array(digest));
}

export async function generateAgentKeyPair(
  agentId: string,
): Promise<AgentKeyPair> {
  const id = agentId.trim().toLowerCase();
  if (!isAgentId(id)) throw new Error(`Not a lowercase UUID: ${agentId}`);
  const pair = await crypto.subtle.generateKey(
    {
      name: "RSASSA-PKCS1-v1_5",
      modulusLength: 3072,
      publicExponent: new Uint8Array([0x01, 0x00, 0x01]),
      hash: "SHA-256",
    },
    true,
    ["sign", "verify"],
  );
  const raw = await crypto.subtle.exportKey("jwk", pair.publicKey);
  const kid = await thumbprint(raw);
  const der = await crypto.subtle.exportKey("pkcs8", pair.privateKey);
  return {
    agentId: id,
    kid,
    privatePem: pem("PRIVATE KEY", der),
    publicJwk: `${JSON.stringify(
      { kty: raw.kty, n: raw.n, e: raw.e, kid, alg: "RS256", use: "sig" },
      null,
      2,
    )}\n`,
    identityJson: `${JSON.stringify(
      { format: "akb-agent-key/v1", agentId: id, kid },
      null,
      2,
    )}\n`,
  };
}

export function archiveName(pair: AgentKeyPair): string {
  return `agent-${pair.agentId}.zip`;
}

export async function keyPairArchive(pair: AgentKeyPair): Promise<Blob> {
  const { default: JSZip } = await import("jszip");
  const zip = new JSZip();
  zip.file("identity.json", pair.identityJson);
  zip.file("public.jwk", pair.publicJwk);
  zip.file("private.pem", pair.privatePem);
  return zip.generateAsync({ type: "blob" });
}
