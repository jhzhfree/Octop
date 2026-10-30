#!/usr/bin/env node
/**
 * Seal keygen.cjs into a single executable using Node's single-executable
 * application support. Build-time only: needs Node 22+ and postject
 * (`npm install` provides it, otherwise it is fetched on demand).
 *
 * The executable embeds a Node runtime, so it still uses the host's
 * icacls.exe / whoami.exe on Windows and POSIX mode bits elsewhere.
 */
import { execFile } from "node:child_process";
import { chmod, copyFile, mkdir, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const exec = promisify(execFile);
// Windows cannot spawn a .cmd shim without a shell (EINVAL).
const shell = process.platform === "win32" ? { shell: true } : {};
const root = path.dirname(fileURLToPath(import.meta.url));
const dist = path.join(root, "dist");
const blob = path.join(dist, "sea-prep.blob");
const target = path.join(
  dist,
  process.platform === "win32" ? "agent-keygen.exe" : "agent-keygen",
);
// The fuse Node.js searches for to detect an injected blob.
const FUSE = "NODE_SEA_FUSE_fce680ab2cc467b6e072b8b5df1996b2";

await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });
await exec(process.execPath, ["--experimental-sea-config", "sea-config.json"], {
  cwd: root,
});
await copyFile(process.execPath, target);
if (process.platform !== "win32") await chmod(target, 0o755);

const args = [target, "NODE_SEA_BLOB", blob, "--sentinel-fuse", FUSE];
try {
  await exec(
    process.platform === "win32" ? "postject.cmd" : "postject",
    args,
    shell,
  );
} catch {
  // postject is a build-time tool only; fetch it rather than shipping it.
  await exec(
    process.platform === "win32" ? "npx.cmd" : "npx",
    // postject has no stable release yet; pin the published alpha.
    ["--yes", "postject@1.0.0-alpha.6", ...args],
    shell,
  );
}

process.stdout.write(`${target}\n`);
if (process.platform === "win32")
  process.stdout.write(
    "Unsigned binary: Windows SmartScreen may warn on first run. Sign it with signtool for distribution.\n",
  );
