import { spawn } from 'node:child_process';
import { WebSocket } from 'ws';

const port='19661', secret='rich4-local-only-test';
const env={...process.env,PORT:port,RICH4_PRIVATE_KEY:secret};
const child=spawn(process.execPath,['rich4-h5/server.mjs'],{env,stdio:['ignore','pipe','pipe']});
let traces='';
child.stdout.on('data',b=>{traces+=b.toString()});child.stderr.on('data',b=>{traces+=b.toString()});
const url='http://127.0.0.1:'+port;
function delay(ms){return new Promise(r=>setTimeout(r,ms))}
async function ready(){for(let i=0;i<80;i++){if(child.exitCode!==null)throw Error('server exited: '+traces);try{const r=await fetch(url+'/health');if(r.ok)return} catch{}await delay(100)}throw Error('startup timeout: '+traces)}
async function makePeer(name,token,ack=0) {
 const ws=new WebSocket('ws://127.0.0.1:'+port+'/relay',{headers:{cookie:'rich4_private='+secret}});
 const inbox=[], waiters=[];
 ws.on('message',b=>{const msg=JSON.parse(b.toString());const idx=waiters.findIndex(w=>w.type===msg.t);if(idx>=0)waiters.splice(idx,1)[0].resolve(msg);else inbox.push(msg)});
 await new Promise((resolve,reject)=>{ws.once('open',resolve);ws.once('error',reject)});
 const wait=type=>{const idx=inbox.findIndex(x=>x.t===type);if(idx>=0)return Promise.resolve(inbox.splice(idx,1)[0]);
 return new Promise((resolve,reject)=>{const w={type,resolve};waiters.push(w);setTimeout(()=>{const i=waiters.indexOf(w);if(i>=0){waiters.splice(i,1);reject(Error('timeout waiting '+type+' from '+name+' '+JSON.stringify(inbox))) }},2500)})};
 ws.send(JSON.stringify({t:'hello',id:name,token,ack}));
 const hi=await wait('ok');
 return {ws,wait,hi,send:obj=>ws.send(JSON.stringify(obj))};
}
try {
 await ready();
 const publicResp=await fetch(url+'/');
 if(publicResp.status!==401)throw Error('unauthed page status '+publicResp.status);
 const login=await fetch(url+'/?access='+secret,{redirect:'manual'});
 if(login.status!==302||!login.headers.get('set-cookie'))throw Error('private login missing');
 const html=await (await fetch(url+'/',{headers:{cookie:'rich4_private='+secret}})).text();
 if(!html.includes('assets/'))throw Error('original Vite index missing');
 const entry=html.match(/(?:src|href)=["']\.\/assets\/([^"']+\.js)["']/)?.[1];
 if(!entry)throw Error('Vite JS entry missing from HTML');
 const bundle=await fetch(url+'/assets/'+entry,{headers:{cookie:'rich4_private='+secret}});
 if(!bundle.ok)throw Error('compiled game JS route failed: '+bundle.status);
 const a=await makePeer('rich4-smoke-a'),b=await makePeer('rich4-smoke-b');
 a.send({t:'conn',cid:'smoke1',to:'rich4-smoke-b',s:1});
 const entered=await b.wait('conn'),opened=await a.wait('open');
 if(entered.from!=='rich4-smoke-a'||opened.cid!=='smoke1')throw Error('connect mismatch');
 a.send({t:'d',cid:'smoke1',d:{t:'board-ready',g:0},s:2});
 const received=await b.wait('d');
 if(received.d.t!=='board-ready')throw Error('message corrupted');
 b.send({t:'ack',a:received.s});
 b.ws.terminate();await delay(140);
 a.send({t:'d',cid:'smoke1',d:{t:'board-go',g:0},s:3});
 await delay(140);
 const b2=await makePeer('rich4-smoke-b',b.hi.token,received.s);
 if(b2.hi.fresh)throw Error('resumption became fresh');
 const pending=await b2.wait('d');
 if(pending.d.t!=='board-go')throw Error('offline queue not replayed');
 a.send({t:'bye'});b2.send({t:'bye'});
 a.ws.close();b2.ws.close();
 console.log('PASS: private authentication, original Vite bundle, room connection, readiness frames and reconnect replay');
} finally {
 child.kill('SIGTERM');
}
