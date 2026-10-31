import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createPublicKey, verify, createHash } from 'node:crypto';
import { generateTrustUUID, generateKeyPair, verifyTerminal, writeProtectedTerminalFile, appendTerminalAudit } from '../dist/terminal.js';
import { readProtectedFile } from '../dist/keystore-node.js';

const hash = value => createHash('sha256').update(value).digest('hex');
test('terminal signs name, UUID, credential and exact request; confirms REST and MCP independently', async t => {
  const root = await mkdtemp(path.join(tmpdir(), 'terminal-sdk-'));
  const workspace = path.join(root, 'workspace'); await mkdir(workspace);
  try {
    const id = generateTrustUUID(); assert.match(id, /^[0-9a-f-]{36}$/);
    const key = await generateKeyPair(path.join(root, 'identity'), id);
    const keyFile = path.join(root, 'service.key'); await writeProtectedTerminalFile(keyFile, 'test-key');
    const binding = { platformId:'opencode', installationId:'install', workspaceId:'workspace', externalAgentId:id,
      runtimeAgentKey:id, policyId:'17', applicationPrincipalId:'123', bindingId:'21', issuer:'https://issuer.example', audience:'akb', resource:'https://akb.example' };
    const calls = [], jtis = new Set(); let wrongAgent = false, omitIdentity = false;
    t.mock.method(globalThis, 'fetch', async (url, options) => {
      assert.equal(options.redirect, 'error'); assert.equal(options.headers['X-API-Key'], 'test-key');
      if (url.includes('/configuration?')) return Response.json({success:true, data:binding});
      const [h,p,s] = options.headers['X-AKB-Workload-Assertion'].split('.');
      assert.ok(verify('RSA-SHA256', Buffer.from(`${h}.${p}`), createPublicKey({key:key.publicJwk,format:'jwk'}), Buffer.from(s,'base64url')));
      const claims = JSON.parse(Buffer.from(p,'base64url'));
      assert.equal(claims.terminal_agent, 'builder'); assert.equal(claims.sub, `agent:${id}`); assert.equal(claims.runtime_agent, id);
      assert.equal(claims.credential_sha256, hash('test-key')); assert.equal(claims.request_binding.body_sha256, hash(options.body ?? ''));
      assert.equal(claims.request_binding.target, new URL(url).pathname); assert.equal(claims.request_binding.method, options.method);
      assert.ok(!jtis.has(claims.jti)); jtis.add(claims.jti); calls.push(url);
      const data = { agentId:wrongAgent ? generateTrustUUID() : id, kid:key.kid, keyStatus:'ACTIVE', policyId:'17', proofProfile:'AGENT_KEY_RS256_V2', runtimeAgentKey:id, terminalAgentName:'builder' };
      if (url.endsWith('/status')) return Response.json({success:true,data});
      const rpc = JSON.parse(Buffer.from(options.body));
      if (rpc.method === 'initialize') return Response.json({jsonrpc:'2.0',id:rpc.id,result:{protocolVersion:'2025-06-18'}});
      assert.equal(rpc.params.name,'get_agent_identity'); assert.deepEqual(rpc.params.arguments,{});
      return Response.json({jsonrpc:'2.0',id:rpc.id,result:{structuredContent:omitIdentity ? {} : data}});
    });
    const options = {agentName:'builder',agentId:id,privateKeyFile:path.join(root,'identity/private.pem'),serviceKeyFile:keyFile,workspaceRoot:workspace,origin:'https://akb.example'};
    const result = await verifyTerminal(options); assert.equal(result.verified,true); assert.equal(calls.length,3);
    omitIdentity = true; await assert.rejects(verifyTerminal(options), /MCP tool did not confirm/);
    omitIdentity = false; wrongAgent = true; await assert.rejects(verifyTerminal(options), /expected key owner/);
    await assert.rejects(generateKeyPair(path.join(root,'identity'),id), /exist/i);
    const audit = path.join(root,'audit.jsonl'); await writeProtectedTerminalFile(audit,'');
    await appendTerminalAudit(audit,workspace,{agentName:'builder',phase:'completed',operation:'search',privateKey:'MUST_NOT_LEAK',headers:'MUST_NOT_LEAK'});
    const event = await readProtectedFile(audit,workspace); assert.equal(JSON.parse(event).agentName,'builder'); assert.ok(!event.includes('MUST_NOT_LEAK'));
  } finally { await rm(root,{recursive:true,force:true}); }
});
