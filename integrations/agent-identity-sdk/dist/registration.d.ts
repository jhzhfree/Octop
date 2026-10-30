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
export declare function proveRegistration(binding: AgentBinding, challenge: RegistrationChallenge, signer: RsaSigner, signal: AbortSignal): Promise<string>;
