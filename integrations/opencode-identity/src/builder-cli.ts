#!/usr/bin/env node
import { readFile } from "node:fs/promises";
import { buildPlugin, inspectOpenCode, verifyPlugin } from "./builder.js";

const [command, ...args] = process.argv.slice(2);
const usage = "Usage: akb-plugin-builder inspect [opencode-executable] [workspace] | generate <input.json> <new-output-directory> [opencode-executable] | verify <identity.json> <workspace> <agent-name>";
try {
  let result;
  if (command === "inspect" && args.length <= 2) result = await inspectOpenCode(args[0], args[1]);
  else if (command === "generate" && [2, 3].includes(args.length)) result = await buildPlugin(JSON.parse(await readFile(args[0], "utf8")), args[1], args[2]);
  else if (command === "verify" && args.length === 3) result = await verifyPlugin(args[0], args[1], args[2]);
  else throw new Error(usage);
  console.log(JSON.stringify(result, null, 2));
} catch (e) {
  // SDK/network/parse exceptions may contain local paths; never echo configuration contents.
  console.error(e instanceof SyntaxError ? "Invalid JSON input" : e instanceof Error ? e.message : "Operation failed");
  process.exitCode = 1;
}
