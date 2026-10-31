import { constants } from "node:fs";
import { mkdir, open, realpath, lstat, rename, unlink } from "node:fs/promises";
import path from "node:path";
import { createPrivateKey, createPublicKey, sign } from "node:crypto";
import { generateKeyMaterial } from "@akb/agent-keygen";
import { bytes, IdentityError, ownerIndex, publicKid, sha256 } from "./core.js";
import { protectionPolicy } from "./permissions.js";
const missing = (e) => e.code === "ENOENT";
const protection = protectionPolicy();
export function outside(workspace, filename) {
    const relative = path.relative(workspace, filename);
    return relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative);
}
export async function readProtectedFile(filename, workspace) {
    if (!path.isAbsolute(filename))
        throw new IdentityError("KEY_UNAVAILABLE", "Identity paths must be absolute");
    await protection.verifyParents(path.dirname(filename));
    if (workspace && !outside(await realpath(workspace), await realpath(filename)))
        throw new IdentityError("KEY_UNAVAILABLE", "Identity material must be outside the Agent workspace");
    await protection.verifyFile(filename);
    const file = await open(filename, constants.O_RDONLY | constants.O_NOFOLLOW);
    try {
        const info = await file.stat();
        if (info.size > 1048576)
            throw new IdentityError("KEY_UNAVAILABLE", "Identity files must be owner-only regular files (0600)");
        protection.verifyHandle(info);
        return await file.readFile("utf8");
    }
    finally {
        await file.close();
    }
}
async function durableCreate(filename, contents) {
    const file = await open(filename, "wx", 0o600);
    try {
        // Protect before any byte is written: POSIX modes apply on create, Windows
        // needs an explicit ACL while the file is still empty.
        await protection.harden(filename, "file");
        await file.writeFile(contents);
        await file.sync();
    }
    finally {
        await file.close();
    }
}
async function atomicWrite(filename, contents) {
    const temporary = `${filename}.${crypto.randomUUID()}.tmp`;
    try {
        await durableCreate(temporary, contents);
        await rename(temporary, filename);
        await protection.syncDirectory(path.dirname(filename));
    }
    finally {
        await unlink(temporary).catch(e => { if (!missing(e))
            throw e; });
    }
}
export async function publicJwk(key) {
    const publicKey = key.type === "public" ? key : createPublicKey(key);
    const raw = publicKey.export({ format: "jwk" });
    if (raw.kty !== "RSA" || !raw.n || !raw.e)
        throw new IdentityError("KEY_UNAVAILABLE", "RSA key required");
    const core = { kty: "RSA", n: raw.n, e: raw.e };
    return Object.freeze({ ...core, kid: await publicKid(core), alg: "RS256", use: "sig" });
}
export class NodeFileKeyStore {
    root;
    constructor(root) {
        this.root = root;
    }
    static async open(root, workspace) {
        if (!path.isAbsolute(root))
            throw new IdentityError("KEY_UNAVAILABLE", "KeyStore root must be absolute");
        await mkdir(root, { recursive: true, mode: 0o700 });
        await protection.harden(root, "directory");
        await protection.verifyParents(root);
        const actual = await realpath(root), info = await lstat(root);
        if (info.isSymbolicLink())
            throw new IdentityError("KEY_UNAVAILABLE", "KeyStore directory must be owned by this account with 0700 permissions");
        await protection.verifyDirectory(actual);
        if (workspace && !outside(await realpath(workspace), actual))
            throw new IdentityError("KEY_UNAVAILABLE", "KeyStore must be outside the Agent workspace");
        return new NodeFileKeyStore(actual);
    }
    async directory(owner) { return path.join(this.root, await sha256(bytes(ownerIndex(owner)))); }
    async manifest(owner) {
        const value = JSON.parse(await readProtectedFile(path.join(await this.directory(owner), "identity.json")));
        if (value.format !== 1 || ownerIndex(value.owner) !== ownerIndex(owner) || !Array.isArray(value.keys) || value.keys.length > 16)
            throw new IdentityError("KEY_UNAVAILABLE", "Invalid identity manifest");
        for (const key of value.keys)
            if (!/^[A-Za-z0-9_-]{43}$/.test(key.kid) || !["PENDING_REGISTRATION", "READY", "RETIRED", "REVOKED"].includes(key.state))
                throw new IdentityError("KEY_UNAVAILABLE", "Invalid key metadata");
        if (new Set(value.keys.map(k => k.kid)).size !== value.keys.length)
            throw new IdentityError("KEY_UNAVAILABLE", "Duplicate key metadata");
        return value;
    }
    async withLock(directory, work) {
        const filename = path.join(directory, ".lock"), start = Date.now();
        let lock;
        while (!lock) {
            try {
                lock = await open(filename, "wx", 0o600);
            }
            catch (e) {
                if (e.code !== "EEXIST")
                    throw e;
                if (Date.now() - start > 10000)
                    throw new IdentityError("KEY_UNAVAILABLE", "KeyStore lock busy; inspect interrupted operation before removing stale lock");
                await new Promise(resolve => setTimeout(resolve, 25));
            }
        }
        try {
            return await work();
        }
        finally {
            await lock.close();
            await unlink(filename);
        }
    }
    async mutate(owner, work) {
        const directory = await this.directory(owner);
        return this.withLock(directory, async () => {
            const manifest = await this.manifest(owner), result = await work(manifest);
            await atomicWrite(path.join(directory, "identity.json"), JSON.stringify(manifest));
            return result;
        });
    }
    async initialize(owner) {
        const directory = await this.directory(owner);
        await mkdir(directory, { mode: 0o700 }).catch(e => { if (e.code !== "EEXIST")
            throw e; });
        await protection.harden(directory, "directory");
        return this.withLock(directory, async () => {
            try {
                const manifest = await this.manifest(owner);
                const selected = manifest.keys.find(k => k.kid === manifest.activeKid) ?? manifest.keys[0];
                if (!selected)
                    throw new IdentityError("KEY_UNAVAILABLE", "Empty identity manifest");
                return await this.readPublic(owner, selected.kid);
            }
            catch (e) {
                if (!missing(e))
                    throw e;
            }
            // Absence of a manifest is only accepted for a genuinely new, empty identity directory.
            const { readdir } = await import("node:fs/promises");
            if ((await readdir(directory)).some(name => name !== ".lock"))
                throw new IdentityError("KEY_UNAVAILABLE", "Interrupted identity initialization requires operator recovery");
            const manifest = { format: 1, owner: { ...owner }, keys: [] };
            const key = await this.generateVersion(owner, manifest);
            await atomicWrite(path.join(directory, "identity.json"), JSON.stringify(manifest));
            return key;
        });
    }
    async generateVersion(owner, manifest) {
        if (manifest.keys.length >= 16)
            throw new IdentityError("KEY_UNAVAILABLE", "Local key history limit reached");
        const pair = await generateKeyMaterial(owner.externalAgentId);
        const jwk = await publicJwk(pair.privateKey), directory = await this.directory(owner);
        const keyDirectory = path.join(directory, jwk.kid);
        await mkdir(keyDirectory, { mode: 0o700 });
        await protection.harden(keyDirectory, "directory");
        await durableCreate(path.join(keyDirectory, "private.pem"), pair.privateKey.export({ format: "pem", type: "pkcs8" }).toString());
        await durableCreate(path.join(keyDirectory, "public.json"), JSON.stringify(jwk));
        const version = { kid: jwk.kid, state: "PENDING_REGISTRATION", createdAt: new Date().toISOString() };
        manifest.keys.push(version);
        return Object.freeze({ owner: Object.freeze({ ...owner }), kid: jwk.kid, publicJwk: jwk, localState: version.state, createdAt: version.createdAt });
    }
    /** Import only through trusted host management IPC; private bytes never leave the host. */
    async importPrivateKey(owner, pem) {
        const key = createPrivateKey(pem), details = key.asymmetricKeyDetails;
        if (key.asymmetricKeyType !== "rsa" || !details || (details.modulusLength ?? 0) < 3072 || (details.modulusLength ?? 0) > 4096 || details.publicExponent !== 65537n)
            throw new IdentityError("KEY_UNAVAILABLE", "RSA 3072–4096 private key required");
        const jwk = await publicJwk(key), directory = await this.directory(owner);
        await mkdir(directory, { recursive: true, mode: 0o700 });
        await protection.harden(directory, "directory");
        return this.withLock(directory, async () => {
            let manifest;
            try {
                manifest = await this.manifest(owner);
            }
            catch (e) {
                if (!missing(e))
                    throw e;
                const { readdir } = await import("node:fs/promises");
                if ((await readdir(directory)).some(name => name !== ".lock"))
                    throw new IdentityError("KEY_UNAVAILABLE", "Interrupted key import requires operator recovery");
                manifest = { format: 1, owner: { ...owner }, keys: [] };
            }
            const existing = manifest.keys.find(v => v.kid === jwk.kid);
            if (existing) {
                if (["RETIRED", "REVOKED"].includes(existing.state))
                    throw new IdentityError("KEY_UNAVAILABLE", "Retired key cannot be imported again");
                return this.readPublic(owner, jwk.kid);
            }
            if (manifest.keys.length >= 16)
                throw new IdentityError("KEY_UNAVAILABLE", "Local key history limit reached");
            const target = path.join(directory, jwk.kid);
            await mkdir(target, { mode: 0o700 });
            await protection.harden(target, "directory");
            await durableCreate(path.join(target, "private.pem"), key.export({ format: "pem", type: "pkcs8" }).toString());
            await durableCreate(path.join(target, "public.json"), JSON.stringify(jwk));
            const version = { kid: jwk.kid, state: "PENDING_REGISTRATION", createdAt: new Date().toISOString() };
            manifest.keys.push(version);
            await atomicWrite(path.join(directory, "identity.json"), JSON.stringify(manifest));
            return Object.freeze({ owner: Object.freeze({ ...owner }), kid: jwk.kid, publicJwk: jwk, localState: version.state, createdAt: version.createdAt });
        });
    }
    async stageRotation(owner) { return this.mutate(owner, manifest => this.generateVersion(owner, manifest)); }
    async describe(owner) {
        let manifest;
        try {
            manifest = await this.manifest(owner);
        }
        catch (error) {
            if (!missing(error))
                throw error;
            const { readdir } = await import("node:fs/promises");
            const entries = await readdir(await this.directory(owner)).catch(error => { if (!missing(error))
                throw error; return []; });
            if (entries.some(name => name !== ".lock"))
                throw new IdentityError("KEY_UNAVAILABLE", "Interrupted identity state requires operator recovery");
            return { activeKid: null, keys: [] };
        }
        return { activeKid: manifest.activeKid ?? null, keys: await Promise.all(manifest.keys.map(key => this.readPublic(owner, key.kid))) };
    }
    async readPublic(owner, kid) {
        const manifest = await this.manifest(owner), version = manifest.keys.find(k => k.kid === kid);
        if (!version)
            throw new IdentityError("KEY_UNAVAILABLE", "Unknown local key version");
        const key = await this.loadPrivate(owner, kid), jwk = await publicJwk(key);
        const stored = JSON.parse(await readProtectedFile(path.join(await this.directory(owner), kid, "public.json")));
        if (jwk.kid !== kid || JSON.stringify(stored) !== JSON.stringify(jwk))
            throw new IdentityError("KEY_UNAVAILABLE", "Key material and metadata mismatch");
        return Object.freeze({ owner: Object.freeze({ ...owner }), kid, publicJwk: jwk, localState: version.state, createdAt: version.createdAt });
    }
    async loadPrivate(owner, kid) {
        if (!/^[A-Za-z0-9_-]{43}$/.test(kid))
            throw new IdentityError("KEY_UNAVAILABLE", "Invalid key version");
        const key = createPrivateKey(await readProtectedFile(path.join(await this.directory(owner), kid, "private.pem")));
        const details = key.asymmetricKeyDetails;
        if (key.asymmetricKeyType !== "rsa" || !details || (details.modulusLength ?? 0) < 3072 || (details.modulusLength ?? 0) > 4096 || details.publicExponent !== 65537n)
            throw new IdentityError("KEY_UNAVAILABLE", "Invalid signing key");
        return key;
    }
    async selectRegisteredKey(owner, kid) {
        await this.readPublic(owner, kid);
        await this.mutate(owner, async (manifest) => {
            const key = manifest.keys.find(k => k.kid === kid);
            if (!key || !["PENDING_REGISTRATION", "READY"].includes(key.state))
                throw new IdentityError("KEY_NOT_REGISTERED", "Key is retired or revoked");
            key.state = "READY";
            manifest.activeKid = kid;
        });
    }
    async openSigner(owner, kid) {
        const metadata = await this.readPublic(owner, kid);
        if (["RETIRED", "REVOKED"].includes(metadata.localState))
            throw new IdentityError("KEY_UNAVAILABLE", "Key is retired or revoked");
        const key = await this.loadPrivate(owner, kid);
        return Object.freeze({ kid, algorithm: "RS256", publicJwk: metadata.publicJwk,
            async sign(message, signal) {
                signal.throwIfAborted();
                const result = await new Promise((resolve, reject) => sign("RSA-SHA256", message, key, (error, signature) => error ? reject(error) : resolve(signature)));
                signal.throwIfAborted();
                return new Uint8Array(result);
            },
        });
    }
    async openActiveSigner(owner) {
        const manifest = await this.manifest(owner), selected = manifest.keys.find(k => k.kid === manifest.activeKid);
        if (!selected || selected.state !== "READY")
            throw new IdentityError("KEY_NOT_REGISTERED", "No registered Agent key selected");
        return this.openSigner(owner, selected.kid);
    }
    async mark(owner, kid, state) {
        await this.mutate(owner, async (manifest) => {
            const key = manifest.keys.find(k => k.kid === kid);
            if (!key || (key.state === "REVOKED" && state !== "REVOKED"))
                throw new IdentityError("KEY_UNAVAILABLE", "Unknown or revoked key");
            key.state = state;
            if (manifest.activeKid === kid)
                delete manifest.activeKid;
        });
    }
    markRetired(owner, kid) { return this.mark(owner, kid, "RETIRED"); }
    markRevoked(owner, kid) { return this.mark(owner, kid, "REVOKED"); }
}
