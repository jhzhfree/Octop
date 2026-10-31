import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { createPublicKey, verify } from 'node:crypto';
import { fileURLToPath } from 'node:url';

/** Absolute path of a built entry point; URL.pathname is unusable on Windows. */
const dist = name => fileURLToPath(new URL(`../dist/${name}`, import.meta.url));
const owner = {platformId:'octop', installationId:'install', workspaceId:'source', externalAgentId:'e21cce9c-1b16-4ab8-a76a-bb761df919b8'};
const call = payload => new Promise((resolve, reject) => {
  const child = spawn(process.execPath, [dist('bridge.js')], {stdio:['pipe','pipe','pipe']});
  let out='', err=''; child.stdout.on('data', c => out+=c); child.stderr.on('data', c=>err+=c); child.on('error',reject);
  child.on('close', code=>{try {assert.equal(err,''); assert.ok(!out.includes('PRIVATE KEY'));resolve({...JSON.parse(out), exitCode:code});} catch(e){reject(e);}});
  child.stdin.end(JSON.stringify(payload));
});

test('host bridge exports public material only, signs matching challenges, and does not select an unverified key', async()=>{
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

test('activation checks remote Agent, policy and kid before selecting the candidate',async()=>{
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

test('setup export emits only public policy inputs, before bindings or keys are prepared',async()=>{
 const {writeFile}=await import('node:fs/promises');const root=await mkdtemp(path.join(tmpdir(),'akb-setup-'));
 try{
  const filename=path.join(root,'config.json');await writeFile(filename,JSON.stringify({configVersion:2,proofProfile:'AGENT_KEY_RS256_V2',...owner,issuer:'https://identity.example/agents',audience:'akb',origin:'https://akb.example',serviceKey:'NEVER_EXPORT_THIS',serviceKeyFile:'/private/key',keyStore:{rootDirectory:'/private/keys'},bindings:[]}),{mode:0o600});
  const result=await new Promise((resolve,reject)=>{const child=spawn(process.execPath,[dist('cli.js'),'setup','export',filename]);let out='',err='';child.stdout.on('data',c=>out+=c);child.stderr.on('data',c=>err+=c);child.on('error',reject);child.on('close',code=>resolve({code,out,err}));});
  assert.equal(result.code,0,result.err);const value=JSON.parse(result.out);assert.equal(value.format,'akb-platform-setup/v1');assert.equal(value.workspaceId,'source');assert.ok(!result.out.includes('NEVER_EXPORT_THIS'));assert.ok(!result.out.includes('/private'));assert.equal(value.bindings,undefined);assert.equal(value.externalAgentId,undefined);
 }finally{await rm(root,{recursive:true,force:true});}
});

test('shipped full and bootstrap configurations export correctly; inconsistent bindings are rejected',async()=>{
 const {writeFile,readFile}=await import('node:fs/promises');const root=await mkdtemp(path.join(tmpdir(),'akb-setup-examples-'));
 const run=async config=>{const filename=path.join(root,'config.json');await writeFile(filename,JSON.stringify(config),{mode:0o600});return new Promise((resolve,reject)=>{const child=spawn(process.execPath,[dist('cli.js'),'setup','export',filename]);let out='';child.stdout.on('data',c=>out+=c);child.stderr.resume();child.on('error',reject);child.on('close',code=>resolve({code,out}));});};
 try{
  for(const name of ['config.example.json','config.bootstrap.example.json']){const config=JSON.parse(await readFile(new URL('../'+name,import.meta.url),'utf8'));const result=await run(config);assert.equal(result.code,0);assert.equal(JSON.parse(result.out).issuer,config.issuer);}
  const config=JSON.parse(await readFile(new URL('../config.example.json',import.meta.url),'utf8'));
  delete config.issuer;delete config.audience;assert.equal((await run(config)).code,0,'existing binding-only configurations remain supported');
  const bad=structuredClone(config);bad.bindings[1].issuer='https://different.example/issuer';assert.equal((await run(bad)).code,1);
  const conflicting=structuredClone(config);conflicting.audience='other';assert.equal((await run(conflicting)).code,1);
 }finally{await rm(root,{recursive:true,force:true});}
});

test('simplified imported keys use one shared key, distinct Agent signatures, and reject unregistered keys',async()=>{
 const {generateKeyPairSync}=await import('node:crypto');const root=await mkdtemp(path.join(tmpdir(),'akb-simple-'));
 const agents=['e21cce9c-1b16-4ab8-a76a-bb761df919b8','01965897-0976-471c-8d64-27eb682a227b'];
 const pairs=agents.map(()=>generateKeyPairSync('rsa',{modulusLength:3072}));let origin;const seen=[];
 const binding=id=>({platformId:'akb-service',installationId:'install',workspaceId:'workspace',externalAgentId:id,runtimeAgentKey:id,policyId:'17',bindingId:'21',applicationPrincipalId:'123',issuer:'https://akb.local/agent-identity',audience:'akb',resource:origin});
 const server=createServer((req,res)=>{
  assert.equal(req.headers['x-api-key'],'shared-key');
  if(req.url.startsWith('/api/v1/external/identity/configuration?')){
   const id=new URL(req.url,origin).searchParams.get('agentId');res.setHeader('Content-Type','application/json');res.end(JSON.stringify({success:true,data:binding(id)}));return;
  }
  const proof=req.headers['x-akb-workload-assertion'];assert.ok(!JSON.stringify(req.headers).includes('PRIVATE KEY'));
  const [h,c,s]=proof.split('.'),claims=JSON.parse(Buffer.from(c,'base64url')),header=JSON.parse(Buffer.from(h,'base64url'));
  const index=agents.indexOf(claims.sub.slice(6));
  if(index<0||!verify('sha256',Buffer.from(`${h}.${c}`),pairs[index].publicKey,Buffer.from(s,'base64url'))){res.writeHead(401);res.end('{}');return;}
  seen.push({id:agents[index],kid:header.kid});res.end(JSON.stringify({success:true,data:{agentId:agents[index],kid:header.kid,proofProfile:'AGENT_KEY_RS256_V2',keyStatus:'ACTIVE',policyId:17}}));
 });server.listen(0,'127.0.0.1');await once(server,'listening');origin=`http://127.0.0.1:${server.address().port}`;
 try{
  const request={op:'connect',root:path.join(root,'keys'),origin,transport:'rest',allowInsecureLoopback:true,apiKey:'shared-key',invocation:{sessionId:'management:1',invocationId:crypto.randomUUID()}};
  for(let i=0;i<2;i++){
   const result=await call({...request,externalAgentId:agents[i],privateKey:pairs[i].privateKey.export({format:'pem',type:'pkcs8'})});assert.equal(result.ok,true);assert.equal(result.data.status.agentId,agents[i]);
   assert.equal((await call({...request,externalAgentId:agents[i]})).ok,true);
  }
  assert.notEqual(seen[0].kid,seen[2].kid);
  const wrong=await call({...request,externalAgentId:agents[0],privateKey:pairs[1].privateKey.export({format:'pem',type:'pkcs8'})});assert.equal(wrong.ok,false);
  const state=await call({op:'state',root:request.root,owner:binding(agents[0])});assert.equal(state.data.activeKid,seen[0].kid);
 }finally{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));await rm(root,{recursive:true,force:true});}
});

test('host generation returns public data only and references cannot cross source scopes', async () => {
 const root=await mkdtemp(path.join(tmpdir(),'akb-host-generation-'));
 try {
  const generated=await call({op:'generate',root:path.join(root,'keys'),keyScope:'source-a',externalAgentId:owner.externalAgentId});
  assert.equal(generated.ok,true);
  assert.equal(generated.data.externalAgentId,owner.externalAgentId);
  assert.ok(generated.data.keyReference);
  assert.equal(generated.data.publicJwk.d,undefined);
  assert.equal(generated.data.privateKey,undefined);
  assert.equal(generated.data.directory,undefined);
  assert.equal((await call({op:'generate',root:path.join(root,'keys'),externalAgentId:owner.externalAgentId})).ok,false);
  const server=createServer((req,res)=>{
    res.setHeader('Content-Type','application/json');
    const resource=`http://127.0.0.1:${server.address().port}`;
    if(req.url.startsWith('/api/v1/external/identity/configuration')) {
      const id=new URL(req.url,resource).searchParams.get('agentId');
      res.end(JSON.stringify({success:true,data:{...owner,externalAgentId:id,runtimeAgentKey:id,resource,policyId:'17',bindingId:'21',applicationPrincipalId:'123',issuer:'https://issuer.example',audience:'akb'}}));return;
    }
    const [h,c,s]=req.headers['x-akb-workload-assertion'].split('.');
    assert.ok(verify('sha256',Buffer.from(`${h}.${c}`),createPublicKey({key:generated.data.publicJwk,format:'jwk'}),Buffer.from(s,'base64url')));
    res.end(JSON.stringify({success:true,data:{agentId:owner.externalAgentId,kid:generated.data.kid,proofProfile:'AGENT_KEY_RS256_V2',keyStatus:'ACTIVE',policyId:17}}));
  });
  server.listen(0,'127.0.0.1');await once(server,'listening');
  try {
    const request={op:'connect',root:path.join(root,'keys'),keyScope:'source-a',externalAgentId:owner.externalAgentId,keyReference:generated.data.keyReference,origin:`http://127.0.0.1:${server.address().port}`,transport:'rest',allowInsecureLoopback:true,apiKey:'shared-key',invocation:{sessionId:'management',invocationId:crypto.randomUUID()}};
    assert.equal((await call({...request,keyScope:'source-b'})).ok,false);
    assert.equal((await call({...request,externalAgentId:crypto.randomUUID()})).ok,false);
    const connected=await call(request);assert.equal(connected.ok,true);assert.equal(connected.data.activeKid,generated.data.kid);
  } finally {server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
 } finally {await rm(root,{recursive:true,force:true});}
});
