/**
 * Filesystem protection for identity material.
 *
 * POSIX enforces account ownership plus 0600/0700 mode bits. Windows has
 * neither, so it enforces the equivalent owner-only ACL through icacls. A host
 * that can enforce neither is refused instead of keeping keys in the open.
 *
 * Windows equivalence notes: the Administrators group and SYSTEM can override
 * file ACLs by privilege (as root can on POSIX), so they are not treated as
 * "other users" for directories. Private key files stay stricter: only the
 * owning account may appear in their ACL.
 */

import { execFile } from "node:child_process";
import { existsSync, type Stats } from "node:fs";
import { lstat, open } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";

import { IdentityError } from "./core.js";

export type ProtectedKind = "file" | "directory";

export interface ProtectionPolicy {
  readonly platform: "posix" | "windows";
  /** Restrict a freshly created file or directory to the owning account. */
  harden(target: string, kind: ProtectedKind): Promise<void>;
  /** Flush the containing directory so an atomic rename is durable. */
  syncDirectory(directory: string): Promise<void>;
  /** Check an already opened file; POSIX re-checks the handle to avoid TOCTOU. */
  verifyHandle(info: Stats): void;
  /** Check a file before secrets are read from it. */
  verifyFile(target: string): Promise<void>;
  /** Check the key store directory itself. */
  verifyDirectory(target: string): Promise<void>;
  /** Check every ancestor directory is not writable by other accounts. */
  verifyParents(target: string): Promise<void>;
}

const FILE_MESSAGE = "Identity files must be owner-only regular files (0600)";
const DIRECTORY_MESSAGE =
  "KeyStore directory must be owned by this account with 0700 permissions";
const PARENT_MESSAGE =
  "Identity parent directories must not be writable by other users";

const posixPolicy: ProtectionPolicy = {
  platform: "posix",
  // mkdir/open modes already apply owner-only bits; umask can only narrow them.
  async harden() {},
  async syncDirectory(directory) {
    const handle = await open(directory, "r");
    try {
      await handle.sync();
    } finally {
      await handle.close();
    }
  },
  verifyHandle(info) {
    if (
      !info.isFile() ||
      info.uid !== process.getuid?.() ||
      (info.mode & 0o077) !== 0
    )
      throw new IdentityError("KEY_UNAVAILABLE", FILE_MESSAGE);
  },
  async verifyFile(target) {
    const info = await lstat(target);
    if (
      !info.isFile() ||
      info.uid !== process.getuid?.() ||
      (info.mode & 0o077) !== 0
    )
      throw new IdentityError("KEY_UNAVAILABLE", FILE_MESSAGE);
  },
  async verifyDirectory(target) {
    const info = await lstat(target);
    if (
      info.isSymbolicLink() ||
      !info.isDirectory() ||
      info.uid !== process.getuid?.() ||
      (info.mode & 0o077) !== 0
    )
      throw new IdentityError("KEY_UNAVAILABLE", DIRECTORY_MESSAGE);
  },
  async verifyParents(directory) {
    for (let current = directory; ; current = path.dirname(current)) {
      const info = await lstat(current);
      if (
        !info.isDirectory() ||
        info.isSymbolicLink() ||
        (info.uid !== 0 && info.uid !== process.getuid?.()) ||
        ((info.mode & 0o022) !== 0 && !((info.mode & 0o1000) !== 0 && info.uid === 0))
      )
        throw new IdentityError("KEY_UNAVAILABLE", PARENT_MESSAGE);
      if (path.dirname(current) === current) break;
    }
  },
};

const SYSTEM_SID = "s-1-5-18";
const ADMINISTRATORS_SID = "s-1-5-32-544";
const CREATOR_OWNER_SID = "s-1-3-0";

let account: { sid: string; name: string } | null = null;

function systemTool(name: string): string {
  const root = process.env.SystemRoot || process.env.windir || "C:\\Windows";
  return path.join(root, "System32", name);
}

async function tool(command: string, args: string[]): Promise<string> {
  const { stdout } = await promisify(execFile)(command, args, {
    encoding: "utf8",
    windowsHide: true,
    timeout: 20000,
    maxBuffer: 1 << 20,
  });
  return stdout;
}

async function ownerAccount() {
  if (account) return account;
  const output = await tool(systemTool("whoami.exe"), ["/user"]);
  const sid = /S-1-\d[\d-]*/.exec(output)?.[0];
  if (!sid)
    throw new IdentityError(
      "KEY_UNAVAILABLE",
      "Unable to resolve the current Windows account",
    );
  account = { sid, name: os.userInfo().username.toLowerCase() };
  return account;
}

interface Ace {
  trustee: string;
  rights: string;
  /** (IO) entries are inherited by children only and never apply to this object. */
  inheritOnly: boolean;
}

/** Parse icacls output into trustees and their rights, ignoring status lines. */
function accessEntries(output: string, target: string): Ace[] {
  const entries: Ace[] = [];
  for (const raw of output.split(/\r?\n/)) {
    let line = raw.trim();
    if (line.toLowerCase().startsWith(target.toLowerCase()))
      line = line.slice(target.length).trim();
    const separator = line.indexOf(":");
    if (separator <= 0) continue;
    const groups = [
      ...line.slice(separator + 1).matchAll(/\(([^)]*)\)/g),
    ].map((match) => match[1]);
    if (!groups.length) continue;
    // icacls prints inheritance flags first and the rights mask last.
    const flags = groups.slice(0, -1);
    entries.push({
      trustee: line.slice(0, separator).trim(),
      rights: groups[groups.length - 1],
      inheritOnly: flags.some((flag) =>
        flag.split(",").some((value) => value.trim().toUpperCase() === "IO"),
      ),
    });
  }
  return entries;
}

/** Compare on the account name or SID, so localized display names still match. */
function principal(trustee: string): string {
  const value = trustee.trim().replace(/^[*@]/, "");
  const slash = value.lastIndexOf("\\");
  return (slash >= 0 ? value.slice(slash + 1) : value).toLowerCase();
}

function isSelf(trustee: string, owner: { sid: string; name: string }): boolean {
  const value = principal(trustee);
  return value === owner.sid.toLowerCase() || value === owner.name;
}

function isPrivilegedSystem(trustee: string): boolean {
  const value = principal(trustee);
  return (
    value === SYSTEM_SID ||
    value === "system" ||
    value === ADMINISTRATORS_SID ||
    value === "administrators"
  );
}

/** CREATOR OWNER only ever applies to whoever created the object. */
function isCreatorOwner(trustee: string): boolean {
  const value = principal(trustee);
  return value === CREATOR_OWNER_SID || value === "creator owner";
}

/** Per-logon-session SID (S-1-5-5-x-y): only processes of this account's own
 * login session carry it, so it belongs to "self" rather than another account. */
function isLogonSession(trustee: string): boolean {
  const value = trustee.trim().toLowerCase().replace(/^[*@]/, "");
  const slash = value.lastIndexOf("\\");
  const name = slash >= 0 ? value.slice(slash + 1) : value;
  return name.startsWith("logonsessionid_") || value.startsWith("s-1-5-5-");
}

/** Integrity labels constrain the requesting process level; they grant no account. */
function isIntegrityLabel(trustee: string): boolean {
  const value = trustee.toLowerCase();
  return (
    value.includes("mandatory label") ||
    value.includes("mandatory level") ||
    value.includes("s-1-16-")
  );
}

/** Rights masks are comma-separated abbreviations; match them exactly, so read
 * masks such as "S,RD,X,RA" or "RX" are not mistaken for write access.
 *
 * "AD" (append data / create child) is deliberately absent: it lets an account
 * add entries to a directory but never touch existing ones, the same allowance
 * POSIX makes for a root-owned sticky directory such as /tmp. */
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

function grantsWrite(rights: string): boolean {
  return rights
    .split(",")
    .some((right) => WRITE_RIGHTS.has(right.trim().toUpperCase()));
}

const windowsPolicy: ProtectionPolicy = {
  platform: "windows",
  async harden(target) {
    const owner = await ownerAccount();
    // Drop inherited entries and leave only the owning account, by SID so the
    // command never depends on a localized account name.
    await tool(systemTool("icacls.exe"), [
      target,
      "/inheritance:r",
      "/grant:r",
      `*${owner.sid}:(F)`,
      "/Q",
    ]);
  },
  // FlushFileBuffers rejects directory handles with EPERM. NTFS journals
  // metadata, so a rename is durable without the extra flush.
  async syncDirectory() {},
  verifyHandle(info) {
    if (!info.isFile())
      throw new IdentityError("KEY_UNAVAILABLE", FILE_MESSAGE);
  },
  async verifyFile(target) {
    // Stat first so a missing file still surfaces as ENOENT; callers probe for
    // it when deciding whether an identity has been prepared yet.
    await lstat(target);
    const owner = await ownerAccount();
    const entries = accessEntries(
      await tool(systemTool("icacls.exe"), [target]),
      target,
    );
    if (!entries.length)
      throw new IdentityError(
        "KEY_UNAVAILABLE",
        "Identity file ACL is unavailable",
      );
    // icacls keeps SYSTEM and the current logon session on every object it
    // touches; both stay inside this account's own context, like root on POSIX.
    if (
      !entries.every(
        (entry) =>
          entry.inheritOnly ||
          isIntegrityLabel(entry.trustee) ||
          isSelf(entry.trustee, owner) ||
          isLogonSession(entry.trustee) ||
          isPrivilegedSystem(entry.trustee),
      )
    )
      throw new IdentityError("KEY_UNAVAILABLE", FILE_MESSAGE);
  },
  async verifyDirectory(target) {
    const info = await lstat(target);
    if (info.isSymbolicLink() || !info.isDirectory())
      throw new IdentityError("KEY_UNAVAILABLE", DIRECTORY_MESSAGE);
    const owner = await ownerAccount();
    const entries = accessEntries(
      await tool(systemTool("icacls.exe"), [target]),
      target,
    );
    if (!entries.length)
      throw new IdentityError(
        "KEY_UNAVAILABLE",
        "KeyStore directory ACL is unavailable",
      );
    if (
      !entries.every(
        (entry) =>
          entry.inheritOnly ||
          isIntegrityLabel(entry.trustee) ||
          isSelf(entry.trustee, owner) ||
          isLogonSession(entry.trustee) ||
          isPrivilegedSystem(entry.trustee),
      )
    )
      throw new IdentityError("KEY_UNAVAILABLE", DIRECTORY_MESSAGE);
  },
  async verifyParents(directory) {
    const owner = await ownerAccount();
    for (let current = directory; ; current = path.dirname(current)) {
      const info = await lstat(current);
      if (info.isSymbolicLink() || !info.isDirectory())
        throw new IdentityError("KEY_UNAVAILABLE", PARENT_MESSAGE);
      const entries = accessEntries(
        await tool(systemTool("icacls.exe"), [current]),
        current,
      );
      for (const entry of entries) {
        if (
          entry.inheritOnly ||
          isIntegrityLabel(entry.trustee) ||
          isSelf(entry.trustee, owner) ||
          isLogonSession(entry.trustee) ||
          isPrivilegedSystem(entry.trustee) ||
          isCreatorOwner(entry.trustee)
        )
          continue;
        if (grantsWrite(entry.rights))
          throw new IdentityError("KEY_UNAVAILABLE", PARENT_MESSAGE);
      }
      if (path.dirname(current) === current) break;
    }
  },
};

export function protectionPolicy(): ProtectionPolicy {
  return process.platform === "win32" ? windowsPolicy : posixPolicy;
}

/** Whether this host can enforce owner-only protection at all. */
export function protectionSupported(): boolean {
  if (process.platform !== "win32") return true;
  return (
    existsSync(systemTool("icacls.exe")) && existsSync(systemTool("whoami.exe"))
  );
}
