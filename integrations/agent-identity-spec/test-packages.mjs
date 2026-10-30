/** Artifact-only smoke test: the consumer receives TGZs, never adjacent SDK/Octop source. */
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {pathToFileURL} from 'node:url';
const packages=process.argv.slice(2);
if(packages.length!==2||packages.some(p=>!path.isAbsolute(p)))throw new Error('Usage: node test-packages.mjs <absolute-sdk.tgz> <absolute-plugin.tgz>');
const root=await mkdtemp(path.join(tmpdir(),'akb-artifact-install-'));
const command=(file,args)=>new Promise((resolve,reject)=>{const p=spawn(file,args,{cwd:root,stdio:['ignore','pipe','pipe']});let out='',err='';p.stdout.on('data',c=>out+=c);p.stderr.on('data',c=>err+=c);p.on('error',reject);p.on('close',code=>resolve({code,out,err}));});
try{
 const install=await command(process.platform==='win32'?'npm.cmd':'npm',['install','--ignore-scripts','--no-audit','--no-fund',...packages]);
 assert.equal(install.code,0,'artifact-only npm install failed');
 const sdk=path.join(root,'node_modules/@akb/agent-identity-sdk'),plugin=path.join(root,'node_modules/@akb/opencode-identity');
 const sdkInfo=JSON.parse(await readFile(path.join(sdk,'package.json'),'utf8')),pluginInfo=JSON.parse(await readFile(path.join(plugin,'package.json'),'utf8'));
 const loaded=await import(pathToFileURL(path.join(plugin,'dist/index.js')).href);assert.equal(typeof loaded.default.server,'function');
 for(const base of [sdk,plugin]){
  assert.ok((await readFile(path.join(base,'LICENSE'),'utf8')).includes('MIT License'));
  if(process.platform!=='win32')for(const filename of [base===sdk?'config.example.json':'config.v2.example.json','config.bootstrap.example.json']){
   const source=JSON.parse(await readFile(path.join(base,filename),'utf8')),configFile=path.join(root,'protected.json');
   await writeFile(configFile,JSON.stringify(source),{mode:0o600});
   const exported=await command(process.execPath,[path.join(sdk,'dist/cli.js'),'setup','export',configFile]);assert.equal(exported.code,0,exported.err);
   const data=JSON.parse(exported.out);assert.equal(data.format,'akb-platform-setup/v1');assert.equal(data.issuer,source.issuer);assert.equal(data.audience,source.audience);assert.equal(data.serviceKeyFile,undefined);assert.equal(data.keyStore,undefined);
  }
 }
 assert.ok((await readFile(path.join(plugin,'README.md'),'utf8')).includes('key select'));
 assert.ok((await readFile(path.join(plugin,'LEGACY-V1.md'),'utf8')).includes('V1'));
 console.log(JSON.stringify({passed:true,artifactOnly:true,sdk:sdkInfo.version,plugin:pluginInfo.version,examplesVerified:process.platform==='win32'?0:4}));
}finally{await rm(root,{recursive:true,force:true});}
