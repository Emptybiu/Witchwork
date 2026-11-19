"""Original mobile multiplayer property board-game prototype.
Not a repackaged Rich4 game or its art assets. Single-process, memory-only rooms.
"""
import asyncio
import json
import os
import random
import secrets
import string
import time
from dataclasses import dataclass, field

from aiohttp import web, WSMsgType

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get('PORT', '10000'))
MAX_ROOMS = 120
MAX_PLAYERS = 4
MAX_MESSAGE_BYTES = 4096
ROOM_TTL_SECONDS = 7200
EMOJI = ['🐱', '🐰', '🦊', '🐼', '🐻', '🐸', '🐨', '🐧']

# 24 squares around a 7x7 board. All content here is original to this prototype.
LABELS = ['起点', '糖果巷', '机遇', '水晶路', '所得税', '森林街', '车站', '花园坊',
          '机遇', '月亮大道', '礼物', '樱桃路', '休息站', '湖畔街', '机遇', '星空路',
          '所得税', '云朵街', '车站', '珊瑚道', '机遇', '梦幻街', '礼物', '皇家路']
TYPES = ['start','land','chance','land','tax','land','rail','land',
         'chance','land','gift','land','rest','land','chance','land',
         'tax','land','rail','land','chance','land','gift','land']
PRICES = [0,1000,0,1200,0,1400,1800,1600,0,1900,0,2200,
          0,2300,0,2600,0,2700,1800,2800,0,3000,0,3200]
BOARD = [dict(i=i, name=LABELS[i], kind=TYPES[i], price=PRICES[i]) for i in range(24)]

@dataclass
class Person:
    id: str
    name: str
    avatar: str
    token: str
    ws: web.WebSocketResponse | None = None
    cash: int = 15000
    pos: int = 0
    ready: bool = False
    loaded: bool = False
    out: bool = False
    shields: int = 0
    last_active: float = field(default_factory=time.monotonic)

@dataclass
class Room:
    code: str
    people: list[Person] = field(default_factory=list)
    host: str = ''
    phase: str = 'lobby'
    turn: int = 0
    round: int = 1
    owners: dict[int,str] = field(default_factory=dict)
    levels: dict[int,int] = field(default_factory=dict)
    pending: int | None = None
    note: str = '等待玩家进入房间'
    updated: float = field(default_factory=time.monotonic)
    version: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

rooms: dict[str, Room] = {}

def clean_name(value):
    return str(value or '玩家').strip()[:12] or '玩家'

def person_by_id(room, pid):
    return next((p for p in room.people if p.id == pid), None)

def alive(room):
    return [p for p in room.people if not p.out]

def score(room, p):
    return p.cash + sum(BOARD[i]['price']*(1+room.levels.get(i,0)*.65) for i,owner in room.owners.items() if owner==p.id)

def snapshot(room):
    room.version += 1
    return dict(t='state', ver=room.version, code=room.code, phase=room.phase,
        host=room.host, turn=room.turn, round=room.round, pending=room.pending,
        note=room.note, board=BOARD,
        people=[dict(id=p.id,name=p.name,avatar=p.avatar,cash=p.cash,pos=p.pos,
                     ready=p.ready, loaded=p.loaded, online=bool(p.ws and not p.ws.closed),
                     out=p.out,shields=p.shields,score=int(score(room,p))) for p in room.people],
        owners=room.owners,levels=room.levels)

async def send(ws, payload):
    if ws and not ws.closed:
        try: await ws.send_json(payload)
        except (ConnectionResetError, RuntimeError): pass

async def broadcast(room):
    room.updated = time.monotonic()
    state = snapshot(room)
    await asyncio.gather(*(send(p.ws,state) for p in room.people))

def new_code():
    for _ in range(20):
        code=''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))
        if code not in rooms: return code
    raise RuntimeError('room codes exhausted')

def bankrupt(room,p):
    if p.cash >= 0: return False
    p.cash=0; p.out=True
    for tile,owner in list(room.owners.items()):
        if owner == p.id:
            del room.owners[tile]; room.levels.pop(tile,None)
    room.note=f'{p.name} 资产耗尽，退出本局'
    return True

def next_turn(room):
    if len(alive(room)) <= 1 or room.round > 24:
        room.phase='ended'; room.pending=None
        winner=max(room.people,key=lambda p:score(room,p))
        room.note=f'本局结束，{winner.name} 获得冠军！'
        return
    prev=room.turn
    for _ in room.people:
        room.turn=(room.turn+1)%len(room.people)
        if room.turn <= prev: room.round+=1
        if not room.people[room.turn].out: break
        prev=room.turn
    if room.round > 24:
        next_turn(room);return
    room.phase='playing';room.pending=None
    room.note=f'轮到 {room.people[room.turn].name} 掷骰子'

def arrive(room,p):
    idx=p.pos; tile=BOARD[idx]
    k=tile['kind']; other=person_by_id(room, room.owners.get(idx))
    room.pending=None
    if k in ('land','rail'):
        if other is None:
            room.pending=idx
            room.note=f'{p.name} 到达{tile["name"]}，可花 ¥{tile["price"]} 购买'
            return
        if other.id == p.id:
            if k=='land' and room.levels.get(idx,0)<2:
                room.pending=idx
                room.note=f'{p.name} 回到自己的{tile["name"]}，可以升级建筑'
                return
            room.note=f'{p.name} 回到自己的{tile["name"]}'
        else:
            rent=int(tile['price']*(.3+.24*room.levels.get(idx,0)))
            if p.shields>0:
                p.shields-=1
                room.note=f'{p.name} 用保护卡免除了 ¥{rent} 租金'
            else:
                p.cash-=rent; other.cash+=rent
                room.note=f'{p.name} 向 {other.name} 支付 ¥{rent} 租金'
    elif k=='chance':
        event=random.choice(['cash','cash','loss','shield','tax'])
        if event=='cash':
            v=random.choice([1200,1800,2500]);p.cash+=v;room.note=f'幸运事件！{p.name} 获得 ¥{v}'
        elif event=='shield':
            p.shields+=1;room.note=f'{p.name} 获得一张免租卡'
        else:
            v=900 if event=='tax' else 1400;p.cash-=v
            room.note=f'{p.name} 遇到意外支出 ¥{v}'
    elif k=='tax':
        p.cash-=1200;room.note=f'{p.name} 缴纳 ¥1200 所得税'
    elif k=='gift':
        p.cash+=1500;room.note=f'{p.name} 领取 ¥1500 惊喜礼金'
    else:
        room.note=f'{p.name} 到达{tile["name"]}，休息一下'
    if bankrupt(room,p): pass
    next_turn_with_note(room)

def next_turn_with_note(room):
    # Show outcome until next action; turn advances in the same synchronized state.
    note=room.note
    next_turn(room)
    if room.phase != 'ended':room.note=note

async def handle_action(room,p,msg):
    action=msg.get('a')
    if action=='ready' and room.phase=='lobby':
        p.ready=bool(msg.get('value'));room.note=f'{p.name} '+('已准备' if p.ready else '取消准备');return
    if action=='start' and room.phase=='lobby' and p.id==room.host:
        if len(room.people)<2 or any(not q.ready for q in room.people):
            return '需要至少两个人，且所有玩家已准备'
        room.phase='preparing';room.note='正在确认所有玩家完成加载'
        for q in room.people:q.loaded=False
        return
    if action=='loaded' and room.phase=='preparing':
        p.loaded=True
        if all(q.loaded for q in room.people):
            room.phase='playing';room.turn=0;room.round=1
            room.note=f'全部加载完成，由 {room.people[0].name} 先手'
        return
    if room.phase!='playing':return
    if room.people[room.turn].id!=p.id: return '请等待自己的回合'
    if action=='roll' and room.pending is None:
        dice=random.randint(1,6)
        old=p.pos;p.pos=(p.pos+dice)%len(BOARD)
        if p.pos<=old:p.cash+=2000
        room.note=f'{p.name} 掷出 {dice} 点'
        arrive(room,p)
        room.note=f'🎲 {dice}点 · '+room.note
    elif action in ('buy','skip') and room.pending is not None:
        idx=room.pending;tile=BOARD[idx];owner=person_by_id(room,room.owners.get(idx))
        cost=tile['price'] if owner is None else int(tile['price']*.65)
        if action=='buy' and p.cash>=cost and (owner is None or owner.id==p.id):
            if owner is None:room.owners[idx]=p.id;room.levels[idx]=0;verb='购买'
            elif tile['kind']=='land' and room.levels.get(idx,0)<2:
                room.levels[idx]+=1;verb='升级'
            else:verb='停留'
            p.cash-=cost;room.note=f'{p.name} 花 ¥{cost} {verb}了{tile["name"]}'
        else:
            room.note=f'{p.name} 放弃了{tile["name"]}'
        room.pending=None;next_turn_with_note(room)
    else:return '当前操作无效'

async def websocket(request):
    ws=web.WebSocketResponse(heartbeat=20, max_msg_size=MAX_MESSAGE_BYTES)
    await ws.prepare(request)
    joined_room=None; member=None
    messages=0; window=time.monotonic()
    async for frame in ws:
        if frame.type != WSMsgType.TEXT:continue
        try: msg=json.loads(frame.data)
        except (json.JSONDecodeError,TypeError):continue
        if not isinstance(msg,dict):continue
        now=time.monotonic()
        if now-window>1:window=now;messages=0
        messages+=1
        if messages>30:
            await ws.close(code=1008,message=b'rate limit');break
        if msg.get('t')=='ping':
            await send(ws,dict(t='pong',at=msg.get('at')));continue
        if msg.get('t') in ('create','join') and not joined_room:
            if msg['t']=='create':
                if len(rooms)>=MAX_ROOMS:
                    await send(ws,dict(t='error',message='房间已满，请稍后重试'));continue
                code=new_code();room=Room(code=code)
                rooms[code]=room
            else:
                code=str(msg.get('code','')).strip().upper()
                room=rooms.get(code)
                if room is None:
                    await send(ws,dict(t='error',message='房间不存在或已过期'));continue
            async with room.lock:
                saved=str(msg.get('token',''))
                existing=next((p for p in room.people if p.token==saved and saved),None)
                if existing:
                    if existing.ws and not existing.ws.closed:await existing.ws.close()
                    member=existing
                else:
                    if room.phase!='lobby':
                        await send(ws,dict(t='error',message='对局已经开始，仅原玩家可重连'));continue
                    if len(room.people)>=MAX_PLAYERS:
                        await send(ws,dict(t='error',message='房间人数已满'));continue
                    avatar=msg.get('avatar','🐱')
                    if avatar not in EMOJI:avatar='🐱'
                    member=Person(id=secrets.token_hex(8),name=clean_name(msg.get('name')),
                                  avatar=avatar,token=secrets.token_urlsafe(30))
                    room.people.append(member)
                    if not room.host:room.host=member.id
                member.ws=ws;member.last_active=now
                joined_room=room
                await send(ws,dict(t='welcome',id=member.id,code=room.code,token=member.token))
                if room.phase=='lobby':room.note=f'{member.name} 进入房间'
                await broadcast(room)
            continue
        if msg.get('t')=='action' and joined_room and member:
            async with joined_room.lock:
                outcome=await handle_action(joined_room,member,msg)
                if outcome:await send(ws,dict(t='error',message=outcome))
                else:await broadcast(joined_room)
    if joined_room and member:
        async with joined_room.lock:
            if member.ws is ws:
                member.ws=None
                if joined_room.phase=='lobby':member.ready=False
                if joined_room.phase=='preparing':member.loaded=False
                await broadcast(joined_room)
    return ws

async def cleanup(app):
    while True:
        await asyncio.sleep(90)
        now=time.monotonic()
        for code,room in list(rooms.items()):
            if (now-room.updated>ROOM_TTL_SECONDS) or (not any(p.ws and not p.ws.closed for p in room.people) and now-room.updated>600):
                rooms.pop(code,None)

async def start_background(app):
    app['cleanup']=asyncio.create_task(cleanup(app))

async def stop_background(app):
    app['cleanup'].cancel()
    try:await app['cleanup']
    except asyncio.CancelledError:pass

async def homepage(request):return web.FileResponse(os.path.join(APP_DIR,'index.html'))
async def health(request):return web.json_response({'ok':True,'rooms':len(rooms)})

def create_app():
    app=web.Application(client_max_size=MAX_MESSAGE_BYTES)
    app.router.add_get('/',homepage)
    app.router.add_get('/ws',websocket)
    app.router.add_get('/healthz',health)
    app.on_startup.append(start_background)
    app.on_cleanup.append(stop_background)
    return app

if __name__=='__main__':
    web.run_app(create_app(),host='0.0.0.0',port=PORT)
