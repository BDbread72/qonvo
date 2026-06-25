"""웹 관리자 페이지 (Operator 전용).  GET /admin

보안 모델:
- **Operator(2) 로그인만 허용** — 비밀번호 검증은 Authenticator(pbkdf2) 재사용.
- **로그인 레이트리밋/락아웃** — IP 별 연속 실패 N회 시 일정 시간 차단(무차별 대입 방지).
- **세션 토큰** — 랜덤(secrets), 서버 메모리에 만료시각과 함께 보관, 사용 시 연장(sliding).
  Authorization: Bearer 헤더로 전달(쿠키 안 씀 → CSRF 불필요). 로그아웃/만료 시 폐기.
- 모든 변경 동작은 세션 검증을 통과해야 함.

⚠️ 한계: 이 서버는 평문 HTTP(TLS 없음)다. 관리자 비밀번호도 평문으로 전송되므로
신뢰할 수 있는 네트워크에서 쓰거나 그 위험을 인지할 것(WS 인증도 동일한 한계).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from aiohttp import web

from . import auth as auth_mod
from .board_store import list_boards, safe_board_id
from .config import get_server_dir

_SESSION_TTL = 12 * 3600   # 세션 수명(초) — 서명 토큰에 만료시각 내장
_MAX_FAILS = 6             # IP 당 연속 로그인 실패 허용
_LOCK_SECS = 300           # 초과 시 차단 시간(초)


def _admin_secret() -> bytes:
    """관리자 세션 서명용 비밀키. 서버 재시작에도 세션이 살아남도록 파일에 영속."""
    p = get_server_dir() / "admin_secret"
    try:
        if p.exists():
            return p.read_bytes()
        s = secrets.token_bytes(32)
        p.write_bytes(s)
        try:
            os.chmod(p, 0o600)
        except Exception:
            pass
        return s
    except Exception:
        # 파일 못 쓰면 휘발성(재시작 시 재로그인) — 최소한 동작은 함
        return secrets.token_bytes(32)


class AdminPanel:
    def __init__(self, server):
        self.s = server
        self._secret = _admin_secret()   # 서명 토큰 키(영속)
        self._fails: dict = {}           # ip -> (count, until)

    def register(self, app: web.Application) -> None:
        app.router.add_get("/admin", self._page)
        app.router.add_post("/admin/api/login", self._login)
        app.router.add_post("/admin/api/logout", self._logout)
        app.router.add_get("/admin/api/state", self._state)
        app.router.add_post("/admin/api/board/delete", self._board_delete)
        app.router.add_post("/admin/api/board/rename", self._board_rename)
        app.router.add_post("/admin/api/user", self._user_action)
        app.router.add_post("/admin/api/say", self._say)
        app.router.add_get("/admin/api/mm/search", self._mm_search)

    # ---- 인증 헬퍼 ------------------------------------------------------
    @staticmethod
    def _ip(req: web.Request) -> str:
        fwd = req.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
        return req.remote or "?"

    def _locked(self, ip: str) -> bool:
        c = self._fails.get(ip)
        return bool(c and c[0] >= _MAX_FAILS and time.time() < c[1])

    def _record_fail(self, ip: str) -> None:
        c = self._fails.get(ip, (0, 0.0))
        self._fails[ip] = (c[0] + 1, time.time() + _LOCK_SECS)

    def _bearer(self, req: web.Request) -> str:
        a = req.headers.get("Authorization", "")
        return a[7:].strip() if a.startswith("Bearer ") else ""

    def _make_token(self, user: str) -> str:
        """HMAC 서명 토큰(payload.sig). 만료시각 내장 → 서버 재시작에도 유효."""
        payload = base64.urlsafe_b64encode(
            json.dumps({"u": user, "exp": int(time.time()) + _SESSION_TTL}).encode()
        ).decode().rstrip("=")
        sig = hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{sig}"

    def _check(self, req: web.Request):
        """서명 토큰 검증(무상태) → username, 아니면 None."""
        tok = self._bearer(req)
        if "." not in tok:
            return None
        payload, _, sig = tok.rpartition(".")
        expected = hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        try:
            pad = "=" * (-len(payload) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload + pad))
        except Exception:
            return None
        if time.time() > data.get("exp", 0):
            return None
        return data.get("u")

    def _require(self, req: web.Request) -> str:
        u = self._check(req)
        if not u:
            raise web.HTTPUnauthorized(
                text=json.dumps({"error": "unauthorized"}), content_type="application/json")
        return u

    # ---- 엔드포인트 -----------------------------------------------------
    async def _login(self, req: web.Request) -> web.Response:
        ip = self._ip(req)
        if self._locked(ip):
            return web.json_response({"error": "너무 많은 시도. 잠시 후 다시."}, status=429)
        try:
            data = await req.json()
        except Exception:
            data = {}
        user = (data.get("user") or "").strip()
        pw = data.get("pass") or ""
        ok, level, _ = self.s.auth.authenticate(user, pw, time.time())
        if not ok:
            self._record_fail(ip)
            return web.json_response({"error": "아이디 또는 비밀번호가 올바르지 않습니다"}, status=401)
        if level < auth_mod.OPERATOR:
            # 자격은 맞지만 권한 부족 — 실패 카운트엔 넣지 않음
            return web.json_response(
                {"error": f"'{user}' 계정은 Operator 권한이 없습니다 (콘솔/관리자에서 op 필요)"}, status=403)
        self._fails.pop(ip, None)
        return web.json_response({"token": self._make_token(user), "user": user})

    async def _logout(self, req: web.Request) -> web.Response:
        # 무상태 토큰이라 서버 보관분 없음 — 클라가 저장소를 비우면 끝
        return web.json_response({"ok": True})

    async def _state(self, req: web.Request) -> web.Response:
        self._require(req)
        boards = []
        for bid in list_boards():
            try:
                b = self.s.boards.get(bid)
                nodes, atts = b.node_count(), len(b.list_attachments())
            except Exception:
                nodes, atts = -1, -1
            try:
                online = len(self.s.registry.board_members(bid))
            except Exception:
                online = 0
            boards.append({"id": bid, "nodes": nodes, "attachments": atts, "online": online})

        roles = auth_mod.list_roles()       # username -> level (핫 오버라이드, merri 포함)
        local = auth_mod.list_users()       # users.json (로컬 id/pw 계정)
        banned = set(auth_mod.list_banned())
        wl = set(auth_mod.list_whitelist())
        online_names = set()
        try:
            online_names = {u[0] for u in self.s.online_users()}
        except Exception:
            pass
        # Mattermost 사용자만: 권한부여/화이트리스트/밴/접속 이력이 있는 사용자명.
        # 로컬 id/pw 계정(users.json: master 등)은 관리 대상에서 제외(부트스트랩 로그인 전용).
        names = set(roles) | banned | wl | online_names

        users = [{
            "name": n,
            "level": roles.get(n, local.get(n)),
            "online": n in online_names,
            "banned": n in banned,
            "whitelisted": n in wl,
        } for n in sorted(names)]

        try:
            usage = self.s.policy.snapshot()
        except Exception:
            usage = []

        return web.json_response({
            "server": self.s.name,
            "online": len(online_names),
            "whitelist_mode": bool(self.s.auth.whitelist_enabled),
            "boards": boards,
            "users": users,
            "usage": usage,
        })

    async def _board_delete(self, req: web.Request) -> web.Response:
        self._require(req)
        data = await req.json()
        bid = safe_board_id(data.get("id", ""))
        try:
            await self.s.registry.broadcast(
                bid, {"type": "server_msg", "text": "이 보드가 관리자에 의해 삭제되었습니다."})
        except Exception:
            pass
        ok = self.s.boards.delete(bid)
        return web.json_response({"ok": ok})

    async def _board_rename(self, req: web.Request) -> web.Response:
        self._require(req)
        data = await req.json()
        ok = self.s.boards.rename(data.get("id", ""), data.get("new", ""))
        return web.json_response({"ok": ok})

    async def _user_action(self, req: web.Request) -> web.Response:
        self._require(req)
        data = await req.json()
        action = data.get("action", "")
        user = (data.get("user") or "").strip()
        if not user:
            return web.json_response({"error": "사용자 없음"}, status=400)
        if action == "op":
            self.s.set_user_level(user, auth_mod.OPERATOR)
        elif action == "deop":
            self.s.set_user_level(user, auth_mod.MEMBER)
        elif action == "setrole":
            self.s.set_user_level(user, int(data.get("level", auth_mod.MEMBER)))
        elif action == "ban":
            auth_mod.ban_user(user)
            await self.s.kick_user(user)
        elif action == "pardon":
            auth_mod.pardon_user(user)
        elif action == "kick":
            await self.s.kick_user(user)
        elif action == "whitelist":
            auth_mod.add_whitelist(user)
        elif action == "unwhitelist":
            auth_mod.remove_whitelist(user)
        elif action == "adduser":
            auth_mod.add_user(user, data.get("pass") or "", int(data.get("level", auth_mod.MEMBER)))
        elif action == "remove":
            auth_mod.remove_user(user)
            auth_mod.clear_role(user)
        else:
            return web.json_response({"error": "알 수 없는 동작"}, status=400)
        return web.json_response({"ok": True})

    async def _say(self, req: web.Request) -> web.Response:
        self._require(req)
        data = await req.json()
        msg = (data.get("msg") or "").strip()
        if msg:
            await self.s.say(f"[Server] {msg}")
        return web.json_response({"ok": True})

    async def _mm_search(self, req: web.Request) -> web.Response:
        """Mattermost 전체 사용자 검색(관리자 본인 토큰 사용) + 현재 권한/상태 주석."""
        admin_user = self._require(req)
        q = (req.query.get("q") or "").strip()
        if not q:
            return web.json_response({"users": []})
        oauth = getattr(self.s, "_oauth", None)
        tok = getattr(oauth, "admin_mm_tokens", {}).get(admin_user) if oauth else None
        if not tok:
            return web.json_response(
                {"error": "Mattermost로 다시 로그인하면 전체 검색이 됩니다.", "need_login": True},
                status=409)
        names = await oauth.search_users(tok, q)
        if names is None:
            oauth.admin_mm_tokens.pop(admin_user, None)
            return web.json_response(
                {"error": "Mattermost 세션 만료 — 다시 로그인하세요.", "need_login": True}, status=409)
        roles = auth_mod.list_roles()
        banned = set(auth_mod.list_banned())
        wl = set(auth_mod.list_whitelist())
        online = set()
        try:
            online = {u[0] for u in self.s.online_users()}
        except Exception:
            pass
        users = [{
            "name": n,
            "level": roles.get(n),
            "banned": n in banned,
            "whitelisted": n in wl,
            "online": n in online,
        } for n in names]
        return web.json_response({"users": users})

    async def _page(self, req: web.Request) -> web.Response:
        return web.Response(text=_HTML, content_type="text/html")


_HTML = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Qonvo 관리자</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#16171a;color:#ddd;font:14px/1.5 system-ui,'Segoe UI',sans-serif}
a,button{font-family:inherit}
.wrap{max-width:920px;margin:0 auto;padding:24px}
h1{font-size:20px;margin:0 0 4px}
.muted{color:#888;font-size:12px}
.card{background:#1f2125;border:1px solid #2c2f34;border-radius:12px;padding:16px;margin:14px 0}
.card h2{font-size:15px;margin:0 0 12px}
input,button,select{background:#2a2d31;color:#eee;border:1px solid #3a3d42;border-radius:8px;padding:8px 12px;font-size:13px}
input::placeholder{color:#777}
button{cursor:pointer}
button.pri{background:#0d6efd;border-color:#0d6efd;color:#fff;font-weight:bold}
button.danger{background:#3a2426;border-color:#5a2a2e;color:#ff8a8a}
button.danger:hover{background:#f04747;color:#fff}
button.sm{padding:5px 10px;font-size:12px}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #2a2d31;font-size:13px}
th{color:#999;font-weight:600;font-size:11px;text-transform:uppercase}
tr:hover td{background:#23262b}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.pill{font-size:11px;padding:2px 8px;border-radius:10px;background:#2c2f34;color:#aaa}
.pill.op{background:#143a1f;color:#7ee29a}.pill.on{background:#13315a;color:#8ab4ff}.pill.ban{background:#3a2426;color:#ff8a8a}
#err{color:#ff8a8a;font-size:12px;min-height:16px}
.hide{display:none}
.topbar{display:flex;justify-content:space-between;align-items:center}
</style></head><body><div class="wrap">

<div id="login" class="card" style="max-width:380px;margin:80px auto">
  <h1>Qonvo 관리자</h1>
  <p class="muted">Operator 계정으로 로그인하세요.</p>
  <div class="row" style="margin-top:12px"><input id="u" placeholder="사용자" style="flex:1"></div>
  <div class="row" style="margin-top:8px"><input id="p" type="password" placeholder="비밀번호" style="flex:1" onkeydown="if(event.key==='Enter')login()"></div>
  <div class="row" style="margin-top:12px"><button class="pri" onclick="login()">로그인</button>
    <button id="merribtn" class="hide" onclick="merriLogin()">Mattermost로 로그인</button></div>
  <div id="err" style="margin-top:8px"></div>
</div>

<div id="app" class="hide">
  <div class="topbar"><div><h1 id="srv">Qonvo</h1><span class="muted" id="stat"></span></div>
    <div class="row"><button class="sm" onclick="load()">새로고침</button><button class="sm" onclick="logout()">로그아웃</button></div></div>

  <div class="card"><h2>보드</h2><table id="boards"><thead><tr><th>ID</th><th>노드</th><th>첨부</th><th>접속</th><th></th></tr></thead><tbody></tbody></table></div>

  <div class="card"><h2>오늘 AI 사용량</h2>
    <div class="muted" style="margin-bottom:8px">서버가 당신 키로 대행한 AI 실행. 토큰 합계 내림차순. 한도는 config.toml [ai_policy] 에서 조정.</div>
    <table id="usage"><thead><tr><th>사용자</th><th>요청</th><th>토큰(in)</th><th>토큰(out)</th><th>실행중</th><th>주 모델</th></tr></thead><tbody></tbody></table>
  </div>

  <div class="card"><h2>공지</h2>
    <div class="row"><input id="say" placeholder="전체 공지 메시지" style="flex:1"><button class="sm pri" onclick="say()">공지</button></div>
  </div>

  <div class="card"><h2>Mattermost 사용자 검색</h2>
    <div class="muted" style="margin-bottom:8px">Mattermost 전체에서 검색해 권한(Op/Member/Visitor)·화이트리스트·밴을 골라 적용하세요.</div>
    <input id="mmq" placeholder="🔍 Mattermost 사용자 검색…" style="width:100%" oninput="mmSearch()">
    <div id="mmres" style="margin-top:10px"></div>
  </div>

  <div class="card"><h2>권한 있는 사용자</h2>
    <div class="muted" style="margin-bottom:8px">op·화이트리스트·밴이 적용됐거나 현재 접속 중인 사용자.</div>
    <table id="users"><thead><tr><th>이름</th><th>레벨</th><th>상태</th><th></th></tr></thead><tbody></tbody></table>
  </div>
</div>

<script>
let TOK = localStorage.getItem('qadmin')||'';
const $ = s=>document.querySelector(s);
function H(){return {'Authorization':'Bearer '+TOK,'Content-Type':'application/json'}}
async function api(path, body){
  const o = body? {method:'POST',headers:H(),body:JSON.stringify(body)} : {headers:H()};
  const r = await fetch('/admin/api/'+path, o);
  if(r.status===401){ logout(); throw new Error('세션 만료'); }
  return r.json();
}
async function login(){
  $('#err').textContent='';
  try{
    const r = await fetch('/admin/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({user:$('#u').value,pass:$('#p').value})});
    const d = await r.json();
    if(!r.ok){ $('#err').textContent=d.error||'실패'; return; }
    TOK=d.token; localStorage.setItem('qadmin',TOK); show(); load();
  }catch(e){ $('#err').textContent='연결 오류'; }
}
function logout(){ if(TOK) api('logout',{}).catch(()=>{}); TOK=''; localStorage.removeItem('qadmin');
  $('#app').classList.add('hide'); $('#login').classList.remove('hide'); }
function show(){ $('#login').classList.add('hide'); $('#app').classList.remove('hide'); }
function esc(s){return (s+'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
async function load(){
  let d; try{ d=await api('state'); }catch(e){ return; }
  $('#srv').textContent=d.server; $('#stat').textContent=`접속 ${d.online}명 · 보드 ${d.boards.length}개`+(d.whitelist_mode?' · 화이트리스트 ON':'');
  $('#boards tbody').innerHTML = d.boards.map(b=>`<tr><td>${esc(b.id)}</td><td>${b.nodes}</td><td>${b.attachments}</td><td>${b.online}</td>
    <td class="row"><button class="sm" onclick="ren('${esc(b.id)}')">이름변경</button><button class="sm danger" onclick="delb('${esc(b.id)}')">삭제</button></td></tr>`).join('')||'<tr><td colspan=5 class=muted>보드 없음</td></tr>';
  const fmt = n => (n||0).toLocaleString();
  $('#usage tbody').innerHTML = (d.usage||[]).map(u=>`<tr><td>${esc(u.user)}</td><td>${u.req}</td>
    <td>${fmt(u.tokens_in)}</td><td>${fmt(u.tokens_out)}</td>
    <td>${u.active>0?`<span class="pill on">${u.active}</span>`:'0'}</td><td>${esc(u.top_model)}</td></tr>`).join('')
    ||'<tr><td colspan=6 class=muted>오늘 사용 없음</td></tr>';
  USERS = d.users || [];
  renderUsers();
}
const LV={0:'Visitor',1:'Member',2:'Operator'};
let USERS=[];
function renderUsers(){
  $('#users tbody').innerHTML = USERS.map(u=>{
    const lvl = u.level==null?'-':(LV[u.level]||u.level);
    const op = u.level===2;
    const tags = (op?'<span class="pill op">OP</span> ':'')+(u.online?'<span class="pill on">접속</span> ':'')
      +(u.whitelisted?'<span class="pill">WL</span> ':'')+(u.banned?'<span class="pill ban">BAN</span>':'');
    const n = esc(u.name);
    let act = `<button class="sm" onclick="ua('${op?'deop':'op'}','${n}')">${op?'deop':'op'}</button>`;
    act += u.whitelisted? `<button class="sm" onclick="ua('unwhitelist','${n}')">WL해제</button>`
                        : `<button class="sm" onclick="ua('whitelist','${n}')">WL추가</button>`;
    act += u.banned? `<button class="sm" onclick="ua('pardon','${n}')">밴해제</button>`
                   : `<button class="sm danger" onclick="ua('ban','${n}')">밴</button>`;
    act += `<button class="sm" onclick="ua('kick','${n}')">kick</button>`;
    return `<tr><td>${n}</td><td>${lvl}</td><td>${tags}</td><td class="row">${act}</td></tr>`;
  }).join('')||'<tr><td colspan=4 class=muted>없음</td></tr>';
}
async function delb(id){ if(!confirm(`보드 '${id}' 삭제? 되돌릴 수 없습니다.`))return; await api('board/delete',{id}); load(); }
async function ren(id){ const n=prompt('새 이름:',id); if(!n||n===id)return; const r=await api('board/rename',{id,new:n}); if(!r.ok)alert('이름변경 실패(중복/오류)'); load(); }
function refresh(){ load(); if($('#mmq') && $('#mmq').value.trim()) doMmSearch(); }
async function ua(action,user){ if((action==='ban'||action==='kick')&&!confirm(`${user} ${action}?`))return; await api('user',{action,user}); refresh(); }
async function uar(user,level){ await api('user',{action:'setrole',user,level}); refresh(); }
async function say(){ const m=$('#say').value.trim(); if(!m)return; await api('say',{msg:m}); $('#say').value=''; }
let mmTimer=null;
function mmSearch(){ clearTimeout(mmTimer); mmTimer=setTimeout(doMmSearch, 300); }
async function doMmSearch(){
  const q=$('#mmq').value.trim();
  if(!q){ $('#mmres').innerHTML=''; return; }
  let d; try{ d=await api('mm/search?q='+encodeURIComponent(q)); }catch(e){ return; }
  if(d.error){ $('#mmres').innerHTML=`<div class="muted">${esc(d.error)}`+(d.need_login?` <button class="sm" onclick="merriLogin()">Mattermost 로그인</button>`:'')+`</div>`; return; }
  if(!d.users || !d.users.length){ $('#mmres').innerHTML='<div class="muted">결과 없음</div>'; return; }
  $('#mmres').innerHTML = d.users.map(u=>{
    const n=esc(u.name); const lvl=u.level==null?'':(LV[u.level]||u.level);
    const tags=(u.level===2?'<span class="pill op">OP</span> ':'')+(u.online?'<span class="pill on">접속</span> ':'')+(u.whitelisted?'<span class="pill">WL</span> ':'')+(u.banned?'<span class="pill ban">BAN</span>':'');
    return `<div class="row" style="justify-content:space-between;border-bottom:1px solid #2a2d31;padding:7px 2px">
      <div>${n} <span class="muted">${lvl}</span> ${tags}</div>
      <div class="row">
        <button class="sm" onclick="uar('${n}',2)">Op</button>
        <button class="sm" onclick="uar('${n}',1)">Member</button>
        <button class="sm" onclick="uar('${n}',0)">Visitor</button>
        ${u.whitelisted?`<button class="sm" onclick="ua('unwhitelist','${n}')">WL해제</button>`:`<button class="sm" onclick="ua('whitelist','${n}')">WL</button>`}
        ${u.banned?`<button class="sm" onclick="ua('pardon','${n}')">밴해제</button>`:`<button class="sm danger" onclick="ua('ban','${n}')">밴</button>`}
      </div></div>`;
  }).join('');
}
function merriLogin(){ location.href='/oauth/mattermost/login?redirect=admin'; }
async function init(){
  // merri 로그인 콜백으로 토큰을 들고 돌아온 경우 자동 로그인
  const q = new URLSearchParams(location.search);
  if(q.get('token') && q.get('user')){
    try{
      const r = await fetch('/admin/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({user:q.get('user'),pass:q.get('token')})});
      const d = await r.json();
      history.replaceState({},'',location.pathname);   // URL 에서 토큰 제거
      if(r.ok){ TOK=d.token; localStorage.setItem('qadmin',TOK); show(); load(); return; }
      $('#err').textContent=d.error||'Mattermost 로그인 실패 (Operator 권한 필요)';
    }catch(e){ history.replaceState({},'',location.pathname); $('#err').textContent='Mattermost 로그인 오류'; }
  }
  // 서버가 merri 인증을 광고하면 버튼 노출
  try{ const h=await (await fetch('/')).json(); if(h.auth&&h.auth.merri) $('#merribtn').classList.remove('hide'); }catch(e){}
  if(TOK){ show(); load(); }
}
init();
</script>
</div></body></html>"""
