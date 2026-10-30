#!/usr/bin/env node
/**
 * Offline Agent key-pair generator. No runtime dependencies, no network, no
 * Octop or AKB involvement: it only creates key material on disk.
 *
 * POSIX enforces owner-only 0600/0700 bits; Windows enforces the equivalent
 * owner-only ACL through icacls. A host that can do neither is refused rather
 * than leaving a private key readable by other accounts. Every artifact is
 * re-checked after it is written, and a failed run removes the directory so no
 * half-protected key survives.
 *
 * CommonJS so the same file can be sealed into a single executable with
 * Node's single-executable application support.
 */
"use strict";

const { execFile } = require("node:child_process");
const { generateKeyPair, createHash, randomUUID } = require("node:crypto");
const { existsSync } = require("node:fs");
const { lstat, mkdir, open, rm } = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { promisify } = require("node:util");

const exec = promisify(execFile);
const generate = promisify(generateKeyPair);

const VERSION = "1.1.0";
const USAGE =
  "Usage: agent-keygen generate --out <new-directory> [--agent-id <existing-UUID>]";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const PROTECTED_PLATFORMS = ["linux", "darwin", "freebsd", "openbsd"];

/* ---------- Windows ACL handling ---------- */

const SYSTEM_SID = "s-1-5-18";
const ADMINISTRATORS_SID = "s-1-5-32-544";
// Rights masks that let a trustee modify or delete; read-only masks such as
// "RX" or "S,RD,X,RA" never appear here.
const WRITE_RIGHTS = new Set([
  "F",
  "M",
  "W",
  "D",
  "WD",
  "WO",
  "WA",
  "GW",
  "GA",
  "DC",
]);

function systemTool(name) {
  const root = process.env.SystemRoot || process.env.windir || "C:\\Windows";
  return path.join(root, "System32", name);
}

async function tool(command, args) {
  const { stdout } = await exec(command, args, {
    encoding: "utf8",
    windowsHide: true,
    timeout: 20000,
    maxBuffer: 1 << 20,
  });
  return stdout;
}

async function windowsOwner() {
  const output = await tool(systemTool("whoami.exe"), [
    "/user",
    "/fo",
    "csv",
    "/nh",
  ]);
  const sid = /S-1-\d[\d-]*/.exec(output)?.[0];
  if (!sid)
    throw new Error("Unable to resolve the current Windows account SID");
  return { sid, name: os.userInfo().username.toLowerCase() };
}

/** Compare on SID or bare account name, so localized display names still match. */
function principal(trustee) {
  const value = trustee.trim().replace(/^[*@]/, "");
  const slash = value.lastIndexOf("\\");
  return (slash >= 0 ? value.slice(slash + 1) : value).toLowerCase();
}

/** SYSTEM and Administrators can override ACLs by privilege, exactly as root
 * can on POSIX, so they are not treated as foreign accounts. */
function isTrusted(trustee, owner) {
  const value = principal(trustee);
  return (
    value === owner.sid.toLowerCase() ||
    value === owner.name ||
    value === SYSTEM_SID ||
    value === "system" ||
    value === ADMINISTRATORS_SID ||
    value === "administrators" ||
    // Per-logon-session SID: only this account's own session carries it.
    value.startsWith("s-1-5-5-") ||
    value.startsWith("logonsessionid_") ||
    // Integrity labels constrain the requesting process; they grant no account.
    value.includes("mandatory label")
  );
}

/** Parse icacls output into trustees, their rights and inheritance-only flags.
 * The target prefix is stripped first: "C:\keys ACCOUNT:(F)" would otherwise
 * split on the drive-letter colon and read the drive as a trustee. */
function accessEntries(output, target) {
  return output
    .split(/\r?\n/)
    .map((raw) => {
      let line = raw.trim();
      if (line.toLowerCase().startsWith(target.toLowerCase()))
        line = line.slice(target.length).trim();
      return line;
    })
    .filter(Boolean)
    .map((line) => {
      const separator = line.indexOf(":");
      if (separator <= 0) return null;
      const groups = [
        ...line.slice(separator + 1).matchAll(/\(([^)]*)\)/g),
      ].map((match) => match[1]);
      if (!groups.length) return null;
      // icacls prints inheritance flags first and the rights mask last.
      const flags = groups.slice(0, -1);
      return {
        trustee: line.slice(0, separator).trim(),
        rights: groups[groups.length - 1].toUpperCase(),
        inheritOnly: flags.some((flag) =>
          flag.split(",").some((value) => value.trim().toUpperCase() === "IO"),
        ),
      };
    })
    .filter(Boolean);
}

function grantsWrite(rights) {
  return rights
    .split(",")
    .some((right) => WRITE_RIGHTS.has(right.trim().toUpperCase()));
}

/* ---------- Protection checks ---------- */

async function verifyWindows(target, owner, message) {
  const entries = accessEntries(
    await tool(systemTool("icacls.exe"), [target]),
    target,
  );
  if (!entries.length) throw new Error(`Unable to read the ACL of ${target}`);
  if (
    !entries.every(
      (entry) => entry.inheritOnly || isTrusted(entry.trustee, owner),
    )
  )
    throw new Error(message);
}

async function verifyPosix(target, kind) {
  const info = await lstat(target);
  const shape =
    kind === "directory"
      ? info.isDirectory() && !info.isSymbolicLink()
      : info.isFile();
  const message =
    kind === "directory"
      ? `Key directory ${target} must be an owner-only directory (0700)`
      : `Key file ${target} must be an owner-only regular file (0600)`;
  if (!shape || info.uid !== process.getuid?.() || (info.mode & 0o077) !== 0)
    throw new Error(message);
}

/** A protected directory is worthless if another account can swap its parent. */
async function verifyParents(directory, owner) {
  if (owner) {
    for (let current = directory; ; current = path.dirname(current)) {
      const entries = accessEntries(
        await tool(systemTool("icacls.exe"), [current]),
        current,
      );
      for (const entry of entries) {
        if (entry.inheritOnly || isTrusted(entry.trustee, owner)) continue;
        if (grantsWrite(entry.rights))
          throw new Error(
            `Parent directory ${current} is writable by another account; choose a parent only you can write to`,
          );
      }
      if (path.dirname(current) === current) break;
    }
    return;
  }
  for (let current = directory; ; current = path.dirname(current)) {
    const info = await lstat(current);
    // A root-owned sticky directory such as /tmp is the POSIX norm, not a leak.
    const stickyRoot = info.uid === 0 && (info.mode & 0o1000) !== 0;
    if (
      !info.isDirectory() ||
      info.isSymbolicLink() ||
      (info.uid !== 0 && info.uid !== process.getuid?.()) ||
      ((info.mode & 0o022) !== 0 && !stickyRoot)
    )
      throw new Error(
        `Parent directory ${current} is writable by another account; choose a parent only you can write to`,
      );
    if (path.dirname(current) === current) break;
  }
}

/* ---------- Generation ---------- */

function parse(argv) {
  if (argv.includes("--version")) return { help: "version" };
  if (!argv.length || argv.includes("--help")) return { help: "usage" };
  if (argv[0] !== "generate" || argv.length % 2 === 0) throw new Error(USAGE);
  const values = {};
  for (let index = 1; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (
      !["--out", "--agent-id"].includes(key) ||
      values[key] !== undefined ||
      !value
    )
      throw new Error(USAGE);
    values[key] = value;
  }
  if (!values["--out"]) throw new Error(USAGE);
  return values;
}

async function main() {
  if (Number(process.versions.node.split(".")[0]) < 22)
    throw new Error("Node.js 22 or newer is required");
  const args = parse(process.argv.slice(2));
  if (args.help === "version") {
    process.stdout.write(`agent-keygen ${VERSION}\n`);
    return;
  }
  if (args.help === "usage") {
    process.stdout.write(`${USAGE}\n`);
    return;
  }

  const agentId = args["--agent-id"] ?? randomUUID();
  if (!UUID.test(agentId)) throw new Error("Agent ID must be a lowercase UUID");
  const output = path.resolve(args["--out"]);

  let owner = null;
  if (process.platform === "win32") {
    if (
      !existsSync(systemTool("icacls.exe")) ||
      !existsSync(systemTool("whoami.exe"))
    )
      throw new Error(
        "Windows hosts need icacls.exe and whoami.exe to protect private keys",
      );
    owner = await windowsOwner();
  } else if (!PROTECTED_PLATFORMS.includes(process.platform)) {
    throw new Error("Unsupported filesystem protection");
  }

  await verifyParents(path.dirname(output), owner);
  // Never overwrite an existing path. Place this directory outside Agent workspaces.
  await mkdir(output, { mode: 0o700 });
  try {
    if (owner)
      await tool(systemTool("icacls.exe"), [
        output,
        "/inheritance:r",
        "/grant:r",
        `*${owner.sid}:(OI)(CI)F`,
        "/Q",
      ]);
    const pair = await generate("rsa", {
      modulusLength: 3072,
      publicExponent: 65537,
    });
    const raw = pair.publicKey.export({ format: "jwk" });
    const kid = createHash("sha256")
      .update(JSON.stringify({ e: raw.e, kty: "RSA", n: raw.n }))
      .digest("base64url");
    const jwk = {
      kty: "RSA",
      n: raw.n,
      e: raw.e,
      kid,
      alg: "RS256",
      use: "sig",
    };
    const files = {
      "private.pem": pair.privateKey.export({ type: "pkcs8", format: "pem" }),
      "public.jwk": `${JSON.stringify(jwk, null, 2)}\n`,
      "identity.json": `${JSON.stringify(
        { format: "akb-agent-key/v1", agentId, kid },
        null,
        2,
      )}\n`,
    };
    for (const [name, contents] of Object.entries(files)) {
      const target = path.join(output, name);
      const handle = await open(target, "wx", 0o600);
      try {
        if (owner)
          await tool(systemTool("icacls.exe"), [
            target,
            "/inheritance:r",
            "/grant:r",
            `*${owner.sid}:F`,
            "/Q",
          ]);
        await handle.writeFile(contents);
        await handle.sync();
      } finally {
        await handle.close();
      }
    }

    // Only report success once the protection is proven, not just requested.
    if (owner) {
      await verifyWindows(
        output,
        owner,
        `Key directory ${output} is not owner-only`,
      );
      for (const name of Object.keys(files))
        await verifyWindows(
          path.join(output, name),
          owner,
          `Key file ${path.join(output, name)} is not owner-only`,
        );
    } else {
      await verifyPosix(output, "directory");
      await verifyPosix(path.join(output, "private.pem"), "file");
    }

    process.stdout.write(
      `${JSON.stringify({ agentId, kid, directory: output })}\n`,
    );
    process.stderr.write(
      `Private key: ${path.join(output, "private.pem")} — keep it on this host, never upload it to AKB or send it to a model.\n`,
    );
  } catch (error) {
    await rm(output, { recursive: true, force: true });
    throw error;
  }
}

main().catch((error) => {
  process.stderr.write(
    `agent-keygen: ${error instanceof Error ? error.message : "key generation failed"}\n${USAGE}\n`,
  );
  process.exitCode = 1;
});
