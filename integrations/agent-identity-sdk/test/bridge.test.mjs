import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { createPublicKey, verify } from 'node:crypto';

const posixTest = process.platform === 'win32' ? test.skip : test;
const owner = {platformId:'octop', installationId:'install', workspaceId:'source', externalAgentId:'e21cce9c-1b16-4ab8-a76a-bb761df919b8'};
const call = payload => new Promise((resolve, reject) => {
  const child = spawn(process.execPath, [new URL('../dist/bridge.js', import.meta.url).pathname], {stdio:['pipe','pipe','pipe']});
  let out='', err=''; child.stdout.on('data', c => out+=c); child.stderr.on('data', c=>err+=c); child.on('error',reject);
  child.on('close', code=>{try {assert.equal(err,''); assert.ok(!out.includes('PRIVATE KEY'));resolve({...JSON.parse(out), exitCode:code});} catch(e){reject(e);}});
  child.stdin.end(JSON.stringify(payload));
});

posixTest('host bridge exports public material only, signs matching challenges, and does not select an unverified key', async()=>{
  const root = await mkdtemp(path.join(tmpdir(),'akb-bridge-'));
  const request={root:path.join(root,'keys'),owner};
  try {
    assert.deepEqual((await call({...request,op:'state'})).data,{activeKid:null,keys:[]});
    const prepared=await call({...request,op:'initialize'});assert.equal(prepared.ok,true);
    const key=prepared.data.keys[0];assert.equal(key.localState,'PENDING_REGISTRATION');assert.equal(prepared.data.activeKid,null);
    assert.deepEqual(Object.keys(key.publicJwk).sort(),['alg','e','kid','kty','n','use']);
    assert.equal((await call({...request,op:'initialize'})).data.keys[0].kid,key.kid);
    const binding={...owner,runtimeAgentKey:'alpha',policyId:'17',bindingId:'21',applicationPrincipalId:'123',issuer:'https://octop.local/agents',audience:'akb',resource:'https://akb.example'};
    const challenge={challengeId:crypto.randomUUID(),nonce:'a'.repeat(43),policyId:'17',bindingId:'21',externalAgentId:owner.externalAgentId,jkt:key.kid,purpose:'akb.agent-key.register',audience:'akb-agent-key-registration',expiresAt:Math.floor(Date.now()/1000)+300};
    const signed=await call({...request,op:'registration',kid:key.kid,binding,challenge});assert.equal(signed.ok,true);
    const [h,p,s]=signed.data.registrationProof.split('.');
    assert.ok(verify('RSA-SHA256',Buffer.from(h+'.'+p),createPublicKey({key:key.publicJwk,format:'jwk'}),Buffer.from(s,'base64url')));
    assert.equal(JSON.parse(Buffer.from(p,'base64url')).nonce,challenge.nonce);
    const denied=await call({...request,op:'registration',kid:key.kid,binding,challenge:{...challenge,externalAgentId:crypto.randomUUID()}});
    assert.equal(denied.ok,false);assert.equal(denied.code,'KEY_NOT_REGISTERED');
    assert.equal((await call({...request,op:'state'})).data.activeKid,null);
  } finally {await rm(root,{recursive:true,force:true});}
});

posixTest('activation checks remote Agent, policy and kid before selecting the candidate',async()=>{
  const root=await mkdtemp(path.join(tmpdir(),'akb-bridge-probe-'));
  let good=false;
  const server=createServer((req,res)=>{
    const [h,p]=req.headers['x-akb-workload-assertion'].split('.');const header=JSON.parse(Buffer.from(h,'base64url')),claims=JSON.parse(Buffer.from(p,'base64url'));
    assert.equal(req.headers['x-api-key'],'shared-key');
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify({success:true,data:{agentId:good?owner.externalAgentId:crypto.randomUUID(),kid:header.kid,policyId:17,proofProfile:'AGENT_KEY_RS256_V2',keyStatus:'ACTIVE',agentPrincipalId:77}}));
  });
  server.listen(0,'127.0.0.1');await once(server,'listening');
  const request={root:path.join(root,'keys'),owner};
  try {
    const key=(await call({...request,op:'initialize'})).data.keys[0];const origin=`http://127.0.0.1:${server.address().port}`;
    const binding={...owner,runtimeAgentKey:'alpha',policyId:'17',bindingId:'21',applicationPrincipalId:'123',issuer:'https://octop.local/agents',audience:'akb',resource:origin};
    const activation={...request,op:'activate',kid:key.kid,binding,origin,transport:'rest',allowInsecureLoopback:true,apiKey:'shared-key',invocation:{sessionId:'management:1',invocationId:crypto.randomUUID()}};
    assert.equal((await call(activation)).ok,false);assert.equal((await call({...request,op:'state'})).data.activeKid,null);
    good=true;const activated=await call(activation);assert.equal(activated.ok,true);assert.equal(activated.data.activeKid,key.kid);
    assert.equal(activated.data.status.agentPrincipalId,77);
  } finally {server.closeAllConnections();await new Promise(resolve=>server.close(resolve));await rm(root,{recursive:true,force:true});}
});

posixTest('setup export emits only public policy inputs, before bindings or keys are prepared',async()=>{
 const {writeFile}=await import('node:fs/promises');const root=await mkdtemp(path.join(tmpdir(),'akb-setup-'));
 try{
  const filename=path.join(root,'config.json');await writeFile(filename,JSON.stringify({configVersion:2,proofProfile:'AGENT_KEY_RS256_V2',...owner,issuer:'https://identity.example/agents',audience:'akb',origin:'https://akb.example',serviceKey:'NEVER_EXPORT_THIS',serviceKeyFile:'/private/key',keyStore:{rootDirectory:'/private/keys'},bindings:[]}),{mode:0o600});
  const result=await new Promise((resolve,reject)=>{const child=spawn(process.execPath,[new URL('../dist/cli.js',import.meta.url).pathname,'setup','export',filename]);let out='',err='';child.stdout.on('data',c=>out+=c);child.stderr.on('data',c=>err+=c);child.on('error',reject);child.on('close',code=>resolve({code,out,err}));});
  assert.equal(result.code,0,result.err);const value=JSON.parse(result.out);assert.equal(value.format,'akb-platform-setup/v1');assert.equal(value.workspaceId,'source');assert.ok(!result.out.includes('NEVER_EXPORT_THIS'));assert.ok(!result.out.includes('/private'));assert.equal(value.bindings,undefined);assert.equal(value.externalAgentId,undefined);
 }finally{await rm(root,{recursive:true,force:true});}
});

posixTest('shipped full and bootstrap configurations export correctly; inconsistent bindings are rejected',async()=>{
 const {writeFile,readFile}=await import('node:fs/promises');const root=await mkdtemp(path.join(tmpdir(),'akb-setup-examples-'));
 const run=async config=>{const filename=path.join(root,'config.json');await writeFile(filename,JSON.stringify(config),{mode:0o600});return new Promise((resolve,reject)=>{const child=spawn(process.execPath,[new URL('../dist/cli.js',import.meta.url).pathname,'setup','export',filename]);let out='';child.stdout.on('data',c=>out+=c);child.stderr.resume();child.on('error',reject);child.on('close',code=>resolve({code,out}));});};
 try{
  for(const name of ['config.example.json','config.bootstrap.example.json']){const config=JSON.parse(await readFile(new URL('../'+name,import.meta.url),'utf8'));const result=await run(config);assert.equal(result.code,0);assert.equal(JSON.parse(result.out).issuer,config.issuer);}
  const config=JSON.parse(await readFile(new URL('../config.example.json',import.meta.url),'utf8'));
  delete config.issuer;delete config.audience;assert.equal((await run(config)).code,0,'existing binding-only configurations remain supported');
  const bad=structuredClone(config);bad.bindings[1].issuer='https://different.example/issuer';assert.equal((await run(bad)).code,1);
  const conflicting=structuredClone(config);conflicting.audience='other';assert.equal((await run(conflicting)).code,1);
 }finally{await rm(root,{recursive:true,force:true});}
});
