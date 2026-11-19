#!/usr/bin/env python3
"""Private H5 patch for the original iam567/rich4-renew-app v1.2.4."""
from pathlib import Path
import os, sys

def replace_one(src, old, new, label):
    n = src.count(old)
    if n != 1:
        raise RuntimeError(f'{label}: expected one anchor, found {n}')
    return src.replace(old, new, 1)

def patch(root):
    base = Path(root) / 'game/src/ui'
    files = {n:(base / n).read_text(encoding='utf-8') for n in ('net-peer.ts','party-net.ts','main.ts')}
    net=files['net-peer.ts']
    net=replace_one(net, "export const RELAYS = ['wss://rich.areu.pro'];", """export const RELAYS = ['wss://rich.areu.pro'];
const H5_RELAY = (import.meta.env.VITE_RICH4_RELAY_URL ?? '').trim();
const H5_RELAY_ONLY = /^wss:\\/\\/[^\\s]+$/.test(H5_RELAY);""", 'wss config')
    net=replace_one(net, "  if (netConfig.relays) return Promise.resolve(netConfig.relays);",
      "  if (netConfig.relays) return Promise.resolve(netConfig.relays);\n  if (H5_RELAY_ONLY && !netConfig.lan) return Promise.resolve([H5_RELAY]);", 'wss only')
    net=replace_one(net, "    if (this.direct) return await this.dialP2P(to) ?? this.dialRelay(to);",
      "    if (H5_RELAY_ONLY) return this.dialRelay(to);\n    if (this.direct) return await this.dialP2P(to) ?? this.dialRelay(to);", 'wss dialing')
    net=replace_one(net, "  if (direct && named) {",
      """  if (H5_RELAY_ONLY) {
    const r = await relayFor(id);
    return r === null || r === 'down' ? null : new NetPeer(id, r, null, peerOpts);
  }
  if (direct && named) {""", 'wss creation')
    files['net-peer.ts']=net
    party=files['party-net.ts']
    party=replace_one(party, "const PREFIX = 'rich4-party-v2-';", "const PREFIX = 'rich4-party-v3-h5-ready-';", 'protocol isolation')
    party=replace_one(party, "  | { t: 'start'; start: StartMsg }",
      """  | { t: 'start'; start: StartMsg }
  | { t: 'board-ready'; g: number }
  | { t: 'board-progress'; g: number; done: number; total: number }
  | { t: 'board-go'; g: number }""", 'messages')
    party=replace_one(party, "  onStart: (s: StartMsg) => void = () => {};",
      """  onStart: (s: StartMsg) => void = () => {};
  private boardGate: {
    gen: number; required: Set<string>; ready: Set<string>; released: boolean;
    finish?: (ok: boolean) => void; progress?: (done: number, total: number) => void;
  } | null = null;
  private clearBoardGate() {
    this.boardGate?.finish?.(false);
    this.boardGate = null;
  }
  private makeBoardGate() {
    this.clearBoardGate();
    this.boardGate = {
      gen: this.gen, required: new Set(Object.keys(this.started?.seats ?? {})),
      ready: new Set(), released: false,
    };
  }
  private gateProgress() {
    const g=this.boardGate;
    if (!g) return;
    const done=[...g.ready].filter(id=>g.required.has(id)).length;
    g.progress?.(done, g.required.size);
    if (this.host) {
      const msg: Msg={t:'board-progress',g:g.gen,done,total:g.required.size};
      for (const c of this.guests.values()) if (c.open) c.send(msg);
    }
  }
  private releaseReadyPlayers() {
    const g=this.boardGate;
    if (!this.host || !g || g.released) return;
    this.gateProgress();
    if (![...g.required].every(id=>g.ready.has(id))) return;
    g.released=true;
    const msg: Msg={t:'board-go',g:g.gen};
    for (const c of this.guests.values()) if (c.open) c.send(msg);
    g.finish?.(true);
  }
  async waitForBoardStart(onProgress: (done:number,total:number)=>void):Promise<boolean> {
    const g=this.boardGate;
    if (!g || this.watching || g.released) return true;
    g.progress=onProgress;
    g.ready.add(this.myId);
    if (this.host) this.releaseReadyPlayers();
    else if (this.toHost?.open) this.toHost.send({t:'board-ready',g:g.gen} satisfies Msg);
    this.gateProgress();
    if (g.released) return true;
    return new Promise(resolve=>{
      const finish=(ok:boolean)=>{clearTimeout(timer);if(this.boardGate===g)this.boardGate=null;resolve(ok);};
      const timer=setTimeout(()=>finish(false),180000);
      g.finish=finish;
      if(g.released)finish(true);
    });
  }""", 'gate')
    party=replace_one(party, "    for (const w of this.connWaiters.splice(0)) w();\n    return true;",
      """    if (this.boardGate?.ready.has(this.myId)) c.send({t:'board-ready',g:this.boardGate.gen} satisfies Msg);
    for (const w of this.connWaiters.splice(0)) w();
    return true;""", 'guest reconnect')
    party=replace_one(party, "      } else if (m.t === 'char') {",
      """      } else if (m.t === 'board-ready') {
        const g=this.boardGate;
        if (g && g.gen===m.g && g.required.has(c.peer)) {g.ready.add(c.peer);this.releaseReadyPlayers();}
      } else if (m.t === 'char') {""", 'host ready receiver')
    party=replace_one(party, "    else if (m.t === 'start') { this.begin(m.start); }",
      """    else if (m.t === 'start') { this.begin(m.start); }
    else if (m.t === 'board-progress') {
      const g=this.boardGate;
      if (g && g.gen===m.g) g.progress?.(m.done,m.total);
    }
    else if (m.t === 'board-go') {
      const g=this.boardGate;
      if (g && g.gen===m.g) {g.released=true;g.finish?.(true);}
    }""", 'guest release')
    party=replace_one(party, "this.begin(m.start); } this.replied?.(''); }",
      "this.begin(m.start, false); } this.replied?.(''); }", 'rejoin bypass')
    party=replace_one(party, "    this.started = s;\n    this.mine = new Set(Object.entries(s.seats).filter(([id]) => id === this.myId).map(([, seat]) => seat));\n    this.remember();\n    this.onMapVote();",
      """    this.started = s;
    this.makeBoardGate();
    this.mine = new Set(Object.entries(s.seats).filter(([id]) => id === this.myId).map(([, seat]) => seat));
    this.remember();
    this.onMapVote();""", 'restart barrier')
    party=replace_one(party, "  start(s: StartMsg) {\n    const m: Msg = { t: 'start', start: s };",
      "  start(s: StartMsg) {\n    if (this.started) return;\n    const m: Msg = { t: 'start', start: s };", 'single start')
    party=replace_one(party, "  private begin(s: StartMsg) {\n    this.started = s;",
      """  private begin(s: StartMsg, awaitAll = true) {
    this.started = s;
    if (awaitAll) this.makeBoardGate(); else this.clearBoardGate();""", 'begin gate')
    party=replace_one(party, "  close() {\n    if (!this.closed) this.onClose();",
      "  close() {\n    this.clearBoardGate();\n    if (!this.closed) this.onClose();", 'cleanup')
    files['party-net.ts']=party
    main=files['main.ts']
    main=replace_one(main, "  if (me !== session) return;\n  // A new game starts with the first background track",
      """  if (me !== session) return;
  // Keep the original loading screen visible until every remote phone has the same map ready.
  if (room && !loaded && !room.watching) {
    const ok=await room.waitForBoardStart((done,total)=>{
      if(me===session)boot.progress(1,'等待玩家加载地图（'+done+'/'+total+'）…');
    });
    if(me!==session)return;
    if(!ok){boot.dispose();room.close();backToTitle('有玩家加载超时或连接断开，请重建房间再试。',false);return;}
  }
  // A new game starts with the first background track""", 'loading barrier')
    files['main.ts']=main
    for name,src in files.items():
        (base/name).write_text(src,encoding='utf-8')
    print('Applied to original Rich4', os.getenv('RICH4_REF','v1.2.4'))

if __name__=='__main__':
    patch(sys.argv[1] if len(sys.argv)>1 else 'upstream')
