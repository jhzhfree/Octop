#!/usr/bin/env node
import { generateKeyPair, createHash, randomUUID } from 'node:crypto';
import { mkdir, open, rm } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import path from 'node:path';
const exec = promisify(execFile);
const generate = promisify(generateKeyPair);
const usage = 'Usage: agent-keygen generate --out <new-directory> [--agent-id <existing-UUID>]';
async function main() {
  if (Number(process.versions.node.split('.')[0]) < 22) throw Error('Node.js 22 or newer is required');
  const args=process.argv.slice(2);
  if(args.includes('--help')){process.stdout.write(usage+'\n');return;}
  if(args.shift()!=='generate'||args.length%2)throw Error(usage);
  const values={};
  for(let i=0;i<args.length;i+=2){if(!['--out','--agent-id'].includes(args[i])||values[args[i]]!==undefined||!args[i+1])throw Error(usage);values[args[i]]=args[i+1];}
  if(!values['--out'])throw Error(usage);
  const agentId=values['--agent-id']??randomUUID();
  if(!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(agentId))throw Error('Agent ID must be a lowercase UUID');
  const output=path.resolve(values['--out']);
  let sid;
  if(process.platform==='win32'){
    const result=await exec('whoami.exe',['/user','/fo','csv','/nh'],{windowsHide:true});sid=result.stdout.match(/S-1-\d+(?:-\d+)+/)?.[0];
    if(!sid)throw Error('Unable to resolve current Windows account SID');
  } else if(!['linux','darwin','freebsd','openbsd'].includes(process.platform))throw Error('Unsupported filesystem protection');
  // Never overwrite an existing path. Place this directory outside Agent workspaces.
  await mkdir(output,{mode:0o700});
  try {
    if(sid)await exec('icacls.exe',[output,'/inheritance:r','/grant:r',`*${sid}:(OI)(CI)F`],{windowsHide:true});
    const pair=await generate('rsa',{modulusLength:3072,publicExponent:65537});
    const raw=pair.publicKey.export({format:'jwk'});
    const kid=createHash('sha256').update(JSON.stringify({e:raw.e,kty:'RSA',n:raw.n})).digest('base64url');
    const jwk={kty:'RSA',n:raw.n,e:raw.e,kid,alg:'RS256',use:'sig'};
    const files={
      'private.pem':pair.privateKey.export({type:'pkcs8',format:'pem'}),
      'public.jwk':JSON.stringify(jwk,null,2)+'\n',
      'identity.json':JSON.stringify({format:'akb-agent-key/v1',agentId,kid},null,2)+'\n',
    };
    for(const [name,contents] of Object.entries(files)){
      const target=path.join(output,name);const handle=await open(target,'wx',0o600);
      try {
        if(sid)await exec('icacls.exe',[target,'/inheritance:r','/grant:r',`*${sid}:F`],{windowsHide:true});
        await handle.writeFile(contents);await handle.sync();
      }finally{await handle.close();}
    }
    process.stdout.write(JSON.stringify({agentId,kid,directory:output})+'\n');
  } catch(e) {await rm(output,{recursive:true,force:true});throw e;}
}
main().catch(()=>{process.stderr.write('Key generation failed. Use a new private directory, check arguments and permissions. '+usage+'\n');process.exitCode=1;});
