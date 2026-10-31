import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { inspectOpenCode, buildPlugin } from '../dist/builder.js';
import { writeProtectedTerminalFile } from '@akb/agent-identity-sdk/terminal';

// Real Windows OpenCode/ACL acceptance is required separately; this fake CLI is a POSIX script.
const posixTest = process.platform === 'win32' ? test.skip : test;
posixTest('builder discovers Agents, preserves UUIDs, creates a loadable plugin and never overwrites keys', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'plugin-builder-'));
  try {
    const workspace = path.join(root,'workspace'); await mkdir(workspace);
    const binary = path.join(root,'opencode');
    await writeFile(binary, '#!/bin/sh\nif [ "$1" = "--version" ]; then echo 1.14.30; else printf "builder (primary)\\nreviewer (subagent)\\n"; fi\n', {mode:0o700});
    assert.deepEqual(await inspectOpenCode(binary), {version:'1.14.30',supported:true,agents:['builder','reviewer']});
    const keyFile = path.join(root,'service.key'); await writeProtectedTerminalFile(keyFile,'test-service-key');
    const id = 'e21cce9c-1b16-4ab8-a76a-bb761df919b8';
    const input = {workspaceRoot:workspace,origin:'https://akb.example',serviceKeyFile:keyFile,agents:[{name:'builder',agentId:id},{name:'reviewer'}]};
    const out = path.join(root,'bundle'); const result = await buildPlugin(input,out,binary);
    const config = JSON.parse(await readFile(result.configFile,'utf8'));
    assert.equal(config.agents[0].agentId,id); assert.notEqual(config.agents[1].agentId,id);
    assert.equal(config.transport,'mcp'); assert.equal(config.agents.length,2);
    const plugin = await import(pathToFileURL(path.join(out,'plugin.mjs')).href);
    assert.equal(typeof plugin.default.server,'function');
    const enrollment = await readFile(path.join(out,'enrollment.json'),'utf8'); assert.ok(!enrollment.includes('PRIVATE KEY'));
    await assert.rejects(buildPlugin(input,out,binary),/exist/i);
    assert.equal(JSON.parse(await readFile(result.configFile,'utf8')).agents[0].agentId,id);
    await assert.rejects(buildPlugin({...input,agents:[{name:'unknown'}]},path.join(root,'unknown'),binary), /unknown Agent/);
    await assert.rejects(buildPlugin({...input,assertionHeader:'X-Evil'},path.join(root,'bad'),binary), /protocol/);
    await writeFile(binary,'#!/bin/sh\nif [ "$1" = "--version" ]; then echo 1.0.0; else echo "builder (primary)"; fi\n',{mode:0o700});
    await assert.rejects(buildPlugin(input,path.join(root,'old'),binary),/not validated/);
  } finally { await rm(root,{recursive:true,force:true}); }
});
