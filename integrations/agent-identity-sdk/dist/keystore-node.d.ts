import { type KeyObject } from "node:crypto";
import type { AgentKeyOwner, AgentKeyStore, PublicAgentKey, RsaPublicJwk, RsaSigner } from "./types.js";
export declare function outside(workspace: string, filename: string): boolean;
export declare function readProtectedFile(filename: string, workspace?: string): Promise<string>;
export declare function publicJwk(key: KeyObject): Promise<RsaPublicJwk>;
export declare class NodeFileKeyStore implements AgentKeyStore {
    readonly root: string;
    private constructor();
    static open(root: string, workspace?: string): Promise<NodeFileKeyStore>;
    private directory;
    private manifest;
    private withLock;
    private mutate;
    initialize(owner: AgentKeyOwner): Promise<PublicAgentKey>;
    private generateVersion;
    /** Import only through trusted host management IPC; private bytes never leave the host. */
    importPrivateKey(owner: AgentKeyOwner, pem: string): Promise<PublicAgentKey>;
    stageRotation(owner: AgentKeyOwner): Promise<Readonly<{
        owner: Readonly<{
            platformId: string;
            installationId: string;
            workspaceId: string;
            externalAgentId: string;
        }>;
        kid: string;
        publicJwk: RsaPublicJwk;
        localState: "PENDING_REGISTRATION" | "READY" | "RETIRED" | "REVOKED";
        createdAt: string;
    }>>;
    describe(owner: AgentKeyOwner): Promise<{
        activeKid: string | null;
        keys: PublicAgentKey[];
    }>;
    readPublic(owner: AgentKeyOwner, kid: string): Promise<PublicAgentKey>;
    private loadPrivate;
    selectRegisteredKey(owner: AgentKeyOwner, kid: string): Promise<void>;
    openSigner(owner: AgentKeyOwner, kid: string): Promise<RsaSigner>;
    openActiveSigner(owner: AgentKeyOwner): Promise<RsaSigner>;
    private mark;
    markRetired(owner: AgentKeyOwner, kid: string): Promise<void>;
    markRevoked(owner: AgentKeyOwner, kid: string): Promise<void>;
}
