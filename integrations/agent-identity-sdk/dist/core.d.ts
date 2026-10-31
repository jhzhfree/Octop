import type { AgentBinding, AgentKeyOwner, BoundRequestProofIssuer, IdentityFailure, RsaPublicJwk, RsaSigner } from "./types.js";
export type * from "./types.js";
export declare class IdentityError extends Error implements IdentityFailure {
    readonly code: IdentityFailure["code"];
    readonly retryable: boolean;
    constructor(code: IdentityFailure["code"], message: string, retryable?: boolean);
    get safeMessage(): string;
}
export declare const identifier: (v: unknown) => v is string;
export declare const uuid: (v: unknown) => v is string;
export declare const traceId: (v: unknown) => v is string;
export declare const bytes: (v: string) => Uint8Array<ArrayBufferLike>;
export declare function base64url(v: Uint8Array): string;
export declare function sha256(v: Uint8Array): Promise<string>;
export declare function publicKid(jwk: Pick<RsaPublicJwk, "e" | "kty" | "n">): Promise<string>;
export declare function ownerIndex(owner: AgentKeyOwner): string;
export declare function ownerOf(binding: AgentBinding): AgentKeyOwner;
export declare function isPrivateHost(hostname: string): boolean;
export declare function canonicalOrigin(value: string, insecureLoopback?: boolean, allowPrivateHttp?: boolean): string;
export declare function signJws(claims: Record<string, unknown>, signer: RsaSigner, type: string, signal: AbortSignal): Promise<string>;
export interface Invocation {
    readonly sessionId: string;
    readonly messageId?: string;
    readonly invocationId: string;
}
export declare function createProofIssuer(binding: AgentBinding, invocation: Invocation, signer: RsaSigner): BoundRequestProofIssuer;
