import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,stat,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {createPrivateKey,createPublicKey,sign,verify} from 'node:crypto';
const exec=promisify(execFile),cli=fileURLToPath(new URL('../keygen.mjs',import.meta.url));
test('offline artifacts match, preserve Agent ID on rotation and never overwrite',async()=>{
 const root=await mkdtemp(path.join(tmpdir(),'agent-keygen-'));
 try{
  const output=path.join(root,'alpha');const result=await exec(process.execPath,[cli,'generate','--out',output]);
  assert.ok(!result.stdout.includes('PRIVATE KEY'));const identity=JSON.parse(await readFile(path.join(output,'identity.json'),'utf8'));
  const jwk=JSON.parse(await readFile(path.join(output,'public.jwk'),'utf8'));assert.equal(jwk.kid,identity.kid);assert.equal(jwk.d,undefined);
  const pem=await readFile(path.join(output,'private.pem'),'utf8');const key=createPrivateKey(pem);assert.equal(key.asymmetricKeyDetails.modulusLength,3072);
  assert.ok(verify('sha256',Buffer.from('request'),createPublicKey({key:jwk,format:'jwk'}),sign('sha256',Buffer.from('request'),key)));
  if(process.platform!=='win32'){assert.equal((await stat(output)).mode&0o777,0o700);assert.equal((await stat(path.join(output,'private.pem'))).mode&0o777,0o600);}
  await assert.rejects(exec(process.execPath,[cli,'generate','--out',output]));assert.equal(await readFile(path.join(output,'private.pem'),'utf8'),pem);
  const rotated=JSON.parse((await exec(process.execPath,[cli,'generate','--out',path.join(root,'rotation'),'--agent-id',identity.agentId])).stdout);
  assert.equal(rotated.agentId,identity.agentId);assert.notEqual(rotated.kid,identity.kid);
 }finally{await rm(root,{recursive:true,force:true});}
});
