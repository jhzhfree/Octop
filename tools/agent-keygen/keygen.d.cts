import type { KeyObject } from "node:crypto";
export interface PublicAgentJwk { kty: "RSA"; n: string; e: string; kid: string; alg: "RS256"; use: "sig"; }
export function generateKeyMaterial(agentId?: string): Promise<{ agentId: string; privateKey: KeyObject; publicJwk: PublicAgentJwk }>;
export function generateToDirectory(options: { directory: string; agentId?: string }): Promise<{ agentId: string; kid: string; directory: string; publicJwk: PublicAgentJwk }>;
