import { IdentityError, signJws, uuid } from "./core.js";
import type { AgentBinding, RsaSigner } from "./types.js";

export interface RegistrationChallenge {
  challengeId: string;
  nonce: string;
  policyId: string;
  bindingId: string;
  externalAgentId: string;
  jkt: string;
  purpose: "akb.agent-key.register";
  audience: "akb-agent-key-registration";
  expiresAt: number;
}
export async function proveRegistration(binding: AgentBinding, challenge: RegistrationChallenge, signer: RsaSigner, signal: AbortSignal) {
  const now = Math.floor(Date.now() / 1000);
  if (!uuid(challenge.challengeId) || !/^[A-Za-z0-9_-]{43}$/.test(challenge.nonce) || challenge.policyId !== binding.policyId
    || challenge.bindingId !== binding.bindingId || challenge.externalAgentId !== binding.externalAgentId || challenge.jkt !== signer.kid
    || challenge.purpose !== "akb.agent-key.register" || challenge.audience !== "akb-agent-key-registration"
    || !Number.isSafeInteger(challenge.expiresAt) || challenge.expiresAt <= now || challenge.expiresAt > now + 330) {
    throw new IdentityError("KEY_NOT_REGISTERED", "Registration challenge does not match the approved key owner");
  }
  return signJws({ ver: 2, purpose: challenge.purpose, aud: challenge.audience, challenge_id: challenge.challengeId,
    nonce: challenge.nonce, policy_id: binding.policyId, binding_id: binding.bindingId, sub: `agent:${binding.externalAgentId}`,
    jkt: signer.kid, iat: now, exp: Math.min(now + 300, challenge.expiresAt) }, signer, "akb-key-registration+jwt", signal);
}
