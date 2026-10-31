/** Trusted terminal management API, including Windows ACL-backed key storage. Not model tools. */
import { randomUUID, createPrivateKey, sign } from "node:crypto";
import { generateToDirectory } from "@akb/agent-keygen";
import { AkbKnowledgeClient } from "./client.js";
import { identifier, IdentityError, uuid } from "./core.js";
import { readProtectedFile, outside, publicJwk } from "./keystore-node.js";
import { constants } from "node:fs";
import { mkdir, open, realpath, rm } from "node:fs/promises";
import path from "node:path";
import { protectionPolicy } from "./permissions.js";
export const generateTrustUUID = () => randomUUID();
export async function generateKeyPair(directory, agentId = generateTrustUUID()) {
    if (!uuid(agentId))
        throw new IdentityError("AGENT_UNREGISTERED", "Invalid trust UUID");
    return generateToDirectory({ directory, agentId });
}
/** Discover public coordinates, then independently prove key possession to REST and MCP. */
export async function verifyTerminal(options, signal = AbortSignal.timeout(60000)) {
    if (!identifier(options.agentName) || !uuid(options.agentId))
        throw new IdentityError("AGENT_UNREGISTERED", "Invalid terminal Agent name or trust UUID");
    const client = new AkbKnowledgeClient({ async readServiceKey(s) {
            s.throwIfAborted();
            return (await readProtectedFile(options.serviceKeyFile, options.workspaceRoot)).trim();
        } }, options.origin, "mcp", options.timeoutMs, options.allowInsecureLoopback, options.allowPrivateHttp);
    const binding = await client.configuration(options.agentId, signal);
    const privateKey = createPrivateKey(await readProtectedFile(options.privateKeyFile, options.workspaceRoot));
    if (privateKey.asymmetricKeyType !== "rsa" || (privateKey.asymmetricKeyDetails?.modulusLength ?? 0) < 3072) {
        throw new IdentityError("KEY_UNAVAILABLE", "RSA 3072 or stronger private key required");
    }
    const jwk = await publicJwk(privateKey), kid = jwk.kid;
    const signer = { kid, algorithm: "RS256", publicJwk: jwk, async sign(input, s) {
            s.throwIfAborted();
            return new Uint8Array(sign("RSA-SHA256", input, privateKey));
        } };
    const invocation = { sessionId: "terminal-verification", invocationId: randomUUID(), terminalAgentName: options.agentName };
    const rest = await client.status(binding, invocation, signer, signal);
    const mcp = await client.identity(binding, invocation, signer, signal);
    return { verified: true, agentName: options.agentName, agentId: options.agentId, rest, mcp };
}
/** Only creates a new directory; caller must choose a trusted parent outside the workspace. */
export async function createProtectedTerminalDirectory(directory, workspaceRoot) {
    if (!path.isAbsolute(directory))
        throw new IdentityError("KEY_UNAVAILABLE", "Absolute output path required");
    const policy = protectionPolicy();
    await policy.verifyParents(path.dirname(directory));
    const actual = path.join(await realpath(path.dirname(directory)), path.basename(directory));
    if (!outside(await realpath(workspaceRoot), actual))
        throw new IdentityError("KEY_UNAVAILABLE", "Output must be outside the Agent workspace");
    await mkdir(directory, { mode: 0o700 });
    try {
        await policy.harden(directory, "directory");
        await policy.verifyDirectory(directory);
    }
    catch (e) {
        await rm(directory, { recursive: true, force: true });
        throw e;
    }
}
export async function writeProtectedTerminalFile(filename, contents) {
    const policy = protectionPolicy();
    await policy.verifyParents(path.dirname(filename));
    await policy.verifyDirectory(path.dirname(filename));
    const handle = await open(filename, "wx", 0o600);
    try {
        await policy.harden(filename, "file");
        await policy.verifyFile(filename);
        await handle.writeFile(contents);
        await handle.sync();
    }
    finally {
        await handle.close();
    }
}
/** Pre-existing, protected audit file. Never accepts headers, arguments or key material. */
export async function appendTerminalAudit(filename, workspaceRoot, event) {
    const policy = protectionPolicy();
    if (!path.isAbsolute(filename) || !outside(await realpath(workspaceRoot), await realpath(filename))) {
        throw new IdentityError("KEY_UNAVAILABLE", "Audit must be outside the Agent workspace");
    }
    await policy.verifyParents(path.dirname(filename));
    await policy.verifyFile(filename);
    const handle = await open(filename, constants.O_WRONLY | constants.O_APPEND | constants.O_NOFOLLOW);
    try {
        policy.verifyHandle(await handle.stat());
        await handle.writeFile(JSON.stringify({ time: new Date().toISOString(), agentName: event.agentName,
            phase: event.phase, operation: event.operation, invocationId: event.invocationId, agentId: event.agentId }) + "\n");
    }
    finally {
        await handle.close();
    }
}
