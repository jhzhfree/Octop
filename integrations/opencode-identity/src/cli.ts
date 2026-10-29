#!/usr/bin/env node
import { generateKeyPairSync, createPrivateKey } from "node:crypto";
import { readFile, writeFile, chmod } from "node:fs/promises";
import { createServer } from "node:http";
import { publicJwk } from "./client.js";

const [command, filename, port] = process.argv.slice(2);
if (!filename || !["keygen", "jwks", "serve-jwks"].includes(command)) {
  console.error("Usage: akb-identity keygen|jwks <private-key-file> OR serve-jwks <private-key-file> <port>"); process.exitCode = 1;
} else if (command === "keygen") {
  const pair = generateKeyPairSync("rsa", { modulusLength: 3072 });
  await writeFile(filename, pair.privateKey.export({ type: "pkcs8", format: "pem" }), { mode: 0o600, flag: "wx" });
  await chmod(filename, 0o600); console.log("RSA key created; run jwks to export the public key set");
} else {
  const key = createPrivateKey(await readFile(filename));
  const document = JSON.stringify({ keys: [publicJwk(key)] });
  if (command === "jwks") console.log(document);
  else {
    const parsed = Number(port);
    if (!Number.isInteger(parsed) || parsed < 1 || parsed > 65535) throw new Error("A valid port is required");
    createServer((request, response) => {
      if (request.method !== "GET" || request.url !== "/.well-known/jwks.json") { response.writeHead(404).end(); return; }
      response.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" }).end(document);
    }).listen(parsed, "127.0.0.1", () => console.log("Public JWKS listener ready on loopback; use an HTTPS reverse proxy in production"));
  }
}
