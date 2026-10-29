/** Checks public design fixtures only. This is not an implementation of the production verifier. */
import assert from "node:assert/strict";
import { createHash, createPublicKey, verify } from "node:crypto";
import { readFile } from "node:fs/promises";

const readJson = async name => JSON.parse(await readFile(new URL(name, import.meta.url), "utf8"));
const fixture = await readJson("./examples/protocol-v2-vectors.json");
const config = await readJson("./examples/config-v2.json");
const hash = bytes => createHash("sha256").update(bytes).digest("hex");
const encode = value => Buffer.from(JSON.stringify(value)).toString("base64url");
let checks = 0;
const check = (value, description) => { assert.ok(value, description); checks++; };

check(config.configVersion === 2 && config.proofProfile === "AGENT_KEY_RS256_V2", "explicit V2 config");
check(config.bindings.length === 2, "two configured Agents");
check(new Set(config.bindings.map(b => b.externalAgentId)).size === 2, "distinct stable identities");
for (const binding of config.bindings) {
  check(binding.platformId === config.platformId && binding.installationId === config.installationId
    && binding.workspaceId === config.workspaceId && binding.resource === config.origin, "consistent binding scope");
}

check(fixture.format === "akb-agent-proof-v2-public-vectors/v1", "known fixture format");
check(fixture.serviceKeyTestValue === "PUBLIC_FIXTURE_NOT_A_REAL_AKB_KEY", "public dummy credential only");
const publicKeys = new Map();
for (const agent of fixture.agents) {
  const jwk = agent.publicJwk;
  check(Object.keys(jwk).every(k => ["kty", "n", "e", "kid", "alg", "use"].includes(k)), "no private key fields");
  const thumbprint = createHash("sha256").update(JSON.stringify({ e: jwk.e, kty: jwk.kty, n: jwk.n })).digest("base64url");
  check(jwk.kid === thumbprint && jwk.alg === "RS256" && jwk.e === "AQAB", "RFC7638 kid and algorithm");
  const key = createPublicKey({ key: jwk, format: "jwk" });
  check(key.asymmetricKeyDetails?.modulusLength === 3072, "3072-bit fixture key");
  publicKeys.set(agent.externalAgentId, key);
}
check(new Set(fixture.agents.map(a => a.publicJwk.kid)).size === fixture.agents.length, "distinct per-Agent public keys");

const seen = new Set();
for (const request of fixture.requests) {
  const [h, p, s] = request.token.split(".");
  check(Boolean(h && p && s) && request.token.split(".").length === 3, "compact JWS");
  const header = JSON.parse(Buffer.from(h, "base64url").toString("utf8"));
  const claims = JSON.parse(Buffer.from(p, "base64url").toString("utf8"));
  const agent = fixture.agents.find(a => a.externalAgentId === request.externalAgentId);
  check(Boolean(agent), "registered fixture Agent");
  const key = publicKeys.get(request.externalAgentId);
  const signingInput = Buffer.from(`${h}.${p}`);
  const signature = Buffer.from(s, "base64url");
  check(header.alg === "RS256" && header.typ === "akb-agent-proof+jwt" && header.kid === agent.publicJwk.kid, "strict header");
  check(verify("RSA-SHA256", signingInput, key, signature), "valid RS256 signature");
  check(claims.ver === 2 && claims.policy_id === fixture.policy.id && claims.azp === fixture.policy.applicationPrincipalId, "version and application");
  check(claims.iss === fixture.policy.issuer && claims.aud === fixture.policy.audience && claims.resource === fixture.policy.resource, "issuer audience resource");
  check(claims.platform_id === fixture.policy.platformId && claims.installation_id === fixture.policy.installationId
    && claims.workspace_id === fixture.policy.workspaceId, "platform and installation");
  check(claims.sub === `agent:${agent.externalAgentId}` && claims.runtime_agent === agent.runtimeAgentKey, "identity binding");
  check(claims.iat === fixture.verificationTime && claims.exp === fixture.verificationTime + 60, "fixed test clock and TTL");
  check(!seen.has(claims.jti), "new jti for each HTTP request"); seen.add(claims.jti);
  const body = Buffer.from(request.bodyBase64, "base64");
  check(body.equals(Buffer.from(request.bodyUtf8, "utf8")), "exact UTF-8 body bytes");
  check(hash(body) === request.bodySha256 && hash(body) === claims.request_binding.body_sha256, "body hash");
  check(claims.request_binding.method === request.method && claims.request_binding.target === request.target, "method and raw target");
  check(claims.credential_sha256 === hash(Buffer.from(fixture.serviceKeyTestValue)), "credential snapshot hash");

  const other = fixture.agents.find(a => a.externalAgentId !== agent.externalAgentId);
  check(!verify("RSA-SHA256", signingInput, publicKeys.get(other.externalAgentId), signature), "other Agent public key rejects signature");
  const changedPayload = encode({ ...claims, sub: `agent:${other.externalAgentId}` });
  check(!verify("RSA-SHA256", Buffer.from(`${h}.${changedPayload}`), key, signature), "claim tampering rejects signature");
  check(hash(Buffer.concat([body, Buffer.from(" ")])) !== claims.request_binding.body_sha256, "body tampering changes binding");
  check(claims.request_binding.target !== `${request.target}&changed=1`, "target tampering differs");
  check(claims.credential_sha256 !== hash(Buffer.from("OTHER_PUBLIC_FIXTURE")), "other credential differs");
}

for (const invalid of fixture.negativeVectors) {
  const [h, p, s] = invalid.token.split(".");
  const header = JSON.parse(Buffer.from(h, "base64url").toString("utf8"));
  const claims = JSON.parse(Buffer.from(p, "base64url").toString("utf8"));
  const actualSigner = fixture.agents.find(a => a.externalAgentId === invalid.actualSigningAgentId);
  const claimedAgent = fixture.agents.find(a => `agent:${a.externalAgentId}` === claims.sub);
  check(verify("RSA-SHA256", Buffer.from(`${h}.${p}`), publicKeys.get(actualSigner.externalAgentId),
    Buffer.from(s, "base64url")), "attack fixture really is signed by Agent A");
  check(actualSigner.externalAgentId !== claimedAgent.externalAgentId, "attack claims another registered Agent");
  check(header.kid !== claimedAgent.publicJwk.kid, "Agent B lookup cannot select Agent A key");
  check(!verify("RSA-SHA256", Buffer.from(`${h}.${p}`), publicKeys.get(claimedAgent.externalAgentId),
    Buffer.from(s, "base64url")), "Agent B public key rejects A-signed impersonation");
}

console.log(`Public design fixtures: ${fixture.requests.length} valid vectors, ${fixture.negativeVectors.length} impersonation vector, ${checks} checks passed. Runtime integration is checked separately.`);
