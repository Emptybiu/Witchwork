import { createServer } from 'node:http';
import { createReadStream, statSync, existsSync } from 'node:fs';
import { join, resolve, extname, sep } from 'node:path';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { WebSocketServer, WebSocket } from 'ws';

const PORT = Number(process.env.PORT || 10000);
const HOST = '0.0.0.0';
const KEY = process.env.RICH4_PRIVATE_KEY || '';
const ROOT = resolve(process.cwd(), 'upstream/game/dist');
const ART = resolve(process.cwd(), 'upstream/game/public/assets');
const SESSIONS = new Map();
const LINKS = new Map();
const HOLD_MS = 60000;
const MAX_FRAME = 256 * 1024;
const MAX_OUT_BYTES = 8 * 1024 * 1024;
const MAX_SESSIONS = 100;

function eq(a,b) {
  const x=Buffer.from(String(a||'')), y=Buffer.from(String(b||''));
  return x.length === y.length && timingSafeEqual(x,y);
}
function auth(req) {
  if (!KEY) return false;  // Refuse accidental open deployment.
  const cookie=(req.headers.cookie||'').split(';').map(s=>s.trim()).find(s=>s.startsWith('rich4_private='));
  return !!cookie && eq(cookie.slice('rich4_private='.length), KEY);
}
function cleanPath(root, rel) {
  let p;
  try { p=decodeURIComponent(rel); } catch { return null; }
  if (p.includes('\0')) return null;
  const file=resolve(root, '.'+sep+p.replace(/^\/+/, ''));
  return file.startsWith(root+sep) ? file : null;
}
const MIME={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.mjs':'text/javascript; charset=utf-8',
  '.css':'text/css; charset=utf-8','.json':'application/json; charset=utf-8','.png':'image/png',
  '.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp','.gif':'image/gif','.svg':'image/svg+xml',
  '.mp3':'audio/mpeg','.ogg':'audio/ogg','.wav':'audio/wav','.mp4':'video/mp4','.webm':'video/webm',
  '.wasm':'application/wasm','.woff2':'font/woff2','.woff':'font/woff','.ttf':'font/ttf'};
const security={'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
  'X-Frame-Options':'DENY','Cache-Control':'no-cache'};
function sendText(res,status,text){res.writeHead(status,{...security,'Content-Type':'text/plain; charset=utf-8'});res.end(text);}
function serveFile(req,res,path) {
  let st;
  try {st=statSync(path); if(!st.isFile())throw Error('not-file');}
  catch{ return sendText(res,404,'Not found'); }
  let start=0,end=st.size-1,code=200;
  const range=req.headers.range;
  if (range && /^bytes=\d*-\d*$/.test(range)) {
    const [a,b]=range.slice(6).split('-');
    if (a) start=Number(a);
    if (b) end=Number(b);
    if (!a && b){start=Math.max(0,st.size-Number(b));end=st.size-1;}
    if(start>=st.size || start<0 || end<start){res.writeHead(416,{'Content-Range':'bytes */'+st.size});return res.end();}
    end=Math.min(end,st.size-1);code=206;
  }
  const headers={...security,'Content-Type':MIME[extname(path).toLowerCase()]||'application/octet-stream',
    'Accept-Ranges':'bytes','Content-Length':String(Math.max(0,end-start+1)),
    'Cache-Control':path.endsWith('index.html')?'no-cache':'private, max-age=43200'};
  if (code===206)headers['Content-Range']='bytes '+start+'-'+end+'/'+st.size;
  res.writeHead(code,headers);
  if(req.method==='HEAD')return res.end();
  const stream=createReadStream(path,{start,end});stream.on('error',()=>res.destroy());stream.pipe(res);
}
const http=createServer((req,res)=>{
  const url=new URL(req.url||'/', 'http://localhost');
  if(url.pathname==='/health')return sendText(res,200,JSON.stringify({up:true,assets:existsSync(join(ART,'maps/maps.json')),active:SESSIONS.size}));
  if(url.pathname==='/stats')return sendText(res,200,JSON.stringify({online:SESSIONS.size,privateRooms:0,waiting:0}));
  if((url.pathname==='/'||url.pathname==='/index.html') && url.searchParams.has('access') && eq(url.searchParams.get('access'),KEY)) {
    res.writeHead(302,{...security,'Location':'/','Set-Cookie':'rich4_private='+KEY+'; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=604800'});
    return res.end();
  }
  if(!auth(req)) {
    if(url.pathname==='/') {
      res.writeHead(401,{...security,'Content-Type':'text/html; charset=utf-8'});
      res.end('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><meta charset="utf-8"><title>私人房间</title><main style="max-width:440px;margin:12vh auto;padding:20px;font:18px system-ui"><h2>大富翁4 · 私人联机</h2><p>请使用房主分享的专属访问链接进入。</p></main>');
      return;
    }
    return sendText(res,401,'Private game');
  }
  if(req.method!=='GET' && req.method!=='HEAD')return sendText(res,405,'GET only');
  let path;
  if(url.pathname.startsWith('/assets/'))path=cleanPath(ART,url.pathname.slice('/assets/'.length));
  else path=cleanPath(ROOT,url.pathname==='/'?'index.html':url.pathname.slice(1));
  if(!path)return sendText(res,403,'Invalid path');
  serveFile(req,res,path);
});

const serverWss=new WebSocketServer({noServer:true,maxPayload:MAX_FRAME,perMessageDeflate:false});
http.on('upgrade',(req,socket,head)=>{
  let url;try{url=new URL(req.url,'http://localhost')}catch{socket.destroy();return}
  if(url.pathname!=='/relay'||!auth(req)){socket.write('HTTP/1.1 403 Forbidden\r\n\r\n');socket.destroy();return}
  serverWss.handleUpgrade(req,socket,head,ws=>serverWss.emit('connection',ws,req));
});
function wire(obj){return JSON.stringify(obj)}
function send(session,obj,reliable=true) {
  if(!session||SESSIONS.get(session.id)!==session)return;
  if(reliable)obj={...obj,s:++session.sent};
  const payload=wire(obj);
  if(reliable) {
    session.queue.set(session.sent,payload);
    session.bytes+=Buffer.byteLength(payload);
    if(session.bytes>MAX_OUT_BYTES)return evict(session,'slow-peer');
  }
  if(session.ws?.readyState===WebSocket.OPEN) {
    try { session.ws.send(payload); }catch{}
  }
}
function evict(s,reason) {
  if(SESSIONS.get(s.id)!==s)return;
  SESSIONS.delete(s.id);
  for (const [cid,ends] of [...LINKS])if(ends.includes(s.id)) {
    LINKS.delete(cid);send(SESSIONS.get(ends.find(v=>v!==s.id)),{t:'close',cid});
  }
  if(s.ws){try{s.ws.close(1001,reason)}catch{}}
  s.ws=null;
}
function ack(s,n) {
  if(!Number.isSafeInteger(n)||n<0)return;
  for(const [seq,payload] of s.queue) {
    if(seq>n)break;
    s.queue.delete(seq);s.bytes-=Buffer.byteLength(payload);
  }
}
function frame(s,m) {
  if(m.t==='p')return send(s,{t:'q',at:m.at},false);
  if(m.t==='ack')return ack(s,m.a);
  if(m.t==='bye')return evict(s,'bye');
  const n=m.s;
  if(!Number.isSafeInteger(n)||n<=0)return;
  if(n<=s.received)return send(s,{t:'ack',a:s.received},false);
  if(n!==s.received+1)return send(s,{t:'ack',a:s.received},false);
  s.received=n;
  const cid=m.cid;
  if(typeof cid!=='string'||cid.length>160||!cid) return send(s,{t:'ack',a:n},false);
  if(m.t==='conn') {
    const dest=SESSIONS.get(m.to);
    if(!dest || dest.id===s.id || LINKS.has(cid))send(s,{t:'nopeer',cid});
    else{
      LINKS.set(cid,[s.id,dest.id]);
      send(dest,{t:'conn',cid,from:s.id});
      send(s,{t:'open',cid});
    }
  } else if(LINKS.has(cid)){
    const ends=LINKS.get(cid);
    if(ends.includes(s.id)){
      const other=SESSIONS.get(ends.find(v=>v!==s.id));
      if(m.t==='d')send(other,{t:'d',cid,d:m.d});
      else if(m.t==='close'){LINKS.delete(cid);send(other,{t:'close',cid})}
    }
  }
  send(s,{t:'ack',a:s.received},false);
}
serverWss.on('connection',ws=>{
  let s=null;const timer=setTimeout(()=>{if(!s)ws.close(1008,'hello timeout')},8000);
  ws.on('message',raw=>{
    if(raw.length>MAX_FRAME)return ws.close(1009,'too big');
    let m;try{m=JSON.parse(raw.toString())}catch{return}
    if(!m||typeof m!=='object')return;
    if(!s){
      if(m.t!=='hello'||typeof m.id!=='string'||!m.id.startsWith('rich4-')||m.id.length>128)return ws.close(1008,'hello required');
      const old=SESSIONS.get(m.id);
      const resumed=!!(old && m.token && eq(m.token,old.token));
      if(old&&!resumed){ws.send(wire({t:'taken'}));return ws.close();}
      if(!old && SESSIONS.size>=MAX_SESSIONS){ws.send(wire({t:'full'}));return ws.close();}
      s=old||{id:m.id,token:randomBytes(24).toString('base64url'),sent:0,received:0,queue:new Map(),bytes:0,ws:null,away:null};
      if(old?.ws && old.ws!==ws){try{old.ws.close(1001,'reconnected')}catch{}}
      s.ws=ws;s.away=null;SESSIONS.set(s.id,s);clearTimeout(timer);
      ack(s,m.ack||0);
      ws.send(wire({t:'ok',token:s.token,ack:s.received,fresh:!resumed}));
      for(const p of s.queue.values())ws.send(p);
      return;
    }
    if(s.ws!==ws)return;
    frame(s,m);
  });
  ws.on('close',()=>{clearTimeout(timer);if(s&&s.ws===ws){s.ws=null;s.away=Date.now()}});
  ws.on('error',()=>{});
});
setInterval(()=>{
  const now=Date.now();
  for(const s of SESSIONS.values()) {
    if(s.away&&now-s.away>HOLD_MS)evict(s,'expired');
    if(s.ws?.readyState===WebSocket.OPEN){try{s.ws.ping()}catch{}}
  }
},5000).unref();
http.listen(PORT,HOST,()=>console.log('Rich4 private H5 listening '+PORT));
