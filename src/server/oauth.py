"""Mattermost(merri) OAuth2 연동.

데스크톱 클라이언트는 username/password 로 WS 인증을 한다. OAuth 는
별도 HTTP 흐름으로 처리해, 로그인 성공 시 **1회성 토큰**을 발급한다.
사용자는 그 토큰을 Connect 다이얼로그의 Password 칸에 붙여넣어 접속한다.

흐름:
  GET  /oauth/mattermost/login     → Mattermost authorize 로 리다이렉트
  GET  /oauth/mattermost/callback  → code 교환 → users/me 조회 → 토큰 발급/표시

Mattermost OAuth2 엔드포인트:
  authorize : {base}/oauth/authorize
  token     : {base}/oauth/access_token
  me        : {base}/api/v4/users/me
"""
from __future__ import annotations

import secrets
import time
from typing import Dict, Tuple
from urllib.parse import urlencode

import aiohttp
from aiohttp import web


class MattermostOAuth:
    """Mattermost OAuth2 핸들러 묶음."""

    def __init__(self, config: dict, authenticator, public_url: str):
        mm = config.get("oauth", {}).get("mattermost", {})
        self.enabled = bool(mm.get("enabled"))
        self.base_url = (mm.get("base_url") or "").rstrip("/")
        self.client_id = mm.get("client_id") or ""
        self.client_secret = mm.get("client_secret") or ""
        self.default_level = int(mm.get("default_level", 1))
        self.allowed_domain = (mm.get("allowed_email_domain") or "").lower().strip()
        self._auth = authenticator
        self._redirect_uri = f"{public_url.rstrip('/')}/oauth/mattermost/callback"
        self._states: Dict[str, dict] = {}  # state -> {exp, loopback}

    def register(self, app: web.Application) -> None:
        """라우트를 등록한다(비활성 시 안내만)."""
        app.router.add_get("/oauth/mattermost/login", self.login)
        app.router.add_get("/oauth/mattermost/callback", self.callback)

    def _new_state(self, now: float, loopback: str = "") -> str:
        # 만료된 state 정리
        for k in [k for k, v in list(self._states.items()) if v.get("exp", 0) < now]:
            self._states.pop(k, None)
        state = secrets.token_urlsafe(16)
        self._states[state] = {"exp": now + 600, "loopback": loopback}
        return state

    async def login(self, request: web.Request) -> web.Response:
        if not self.enabled:
            return web.Response(text="Mattermost OAuth is disabled in config.toml", status=404)
        if not (self.base_url and self.client_id):
            return web.Response(text="OAuth not configured (base_url/client_id)", status=500)
        now = time.time()
        # 클라(데스크톱) 프로필 로그인: loopback 콜백으로 결과를 돌려준다.
        loopback = request.query.get("redirect", "")
        if loopback and not loopback.startswith(("http://127.0.0.1:", "http://localhost:")):
            loopback = ""  # 로컬 루프백만 허용(보안)
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self._redirect_uri,
            "state": self._new_state(now, loopback),
        }
        url = f"{self.base_url}/oauth/authorize?{urlencode(params)}"
        raise web.HTTPFound(url)

    async def callback(self, request: web.Request) -> web.Response:
        if not self.enabled:
            return web.Response(text="Mattermost OAuth is disabled", status=404)
        now = time.time()
        code = request.query.get("code", "")
        state = request.query.get("state", "")
        entry = self._states.pop(state, None)
        if not code or entry is None:
            return web.Response(text="Invalid OAuth callback (code/state)", status=400)
        loopback = entry.get("loopback", "")

        try:
            username, email, access_token = await self._exchange_and_fetch(code)
        except Exception as e:
            return web.Response(text=f"OAuth failed: {e}", status=502)

        if self.allowed_domain and not email.lower().endswith("@" + self.allowed_domain):
            return web.Response(text=f"Email domain not allowed: {email}", status=403)

        # 데스크톱 프로필 로그인: merri 토큰 + merri 주소를 loopback 으로 돌려준다.
        # (People 패널이 merri API 를 직접 호출하려면 base_url 이 필요)
        if loopback:
            from urllib.parse import quote
            url = (f"{loopback}?token={quote(access_token)}&user={quote(username)}"
                   f"&merri={quote(self.base_url)}")
            raise web.HTTPFound(url)

        # 구버전: 1회용 qonvo 토큰을 화면에 표시(붙여넣기)
        token = self._auth.tokens.issue(username, self.default_level, now)
        return web.Response(text=self._success_page(username, token), content_type="text/html")

    async def validate_token(self, merri_token: str):
        """merri 토큰을 chat 서버에 검증하고 (username, email) 반환. 실패 시 None."""
        if not (self.enabled and self.base_url and merri_token):
            return None
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
        try:
            async with aiohttp.ClientSession(headers={"User-Agent": ua}) as sess:
                async with sess.get(f"{self.base_url}/api/v4/users/me",
                                    headers={"Authorization": f"Bearer {merri_token}"}) as r:
                    if r.status != 200:
                        return None
                    me = await r.json()
            uname, email = me.get("username", ""), me.get("email", "")
            if not uname:
                return None
            if self.allowed_domain and not email.lower().endswith("@" + self.allowed_domain):
                return None
            return uname, email
        except Exception:
            return None

    async def _exchange_and_fetch(self, code: str) -> Tuple[str, str, str]:
        """code 를 토큰으로 교환하고 (username, email, access_token) 을 반환한다."""
        # Cloudflare 등이 Python UA 를 봇으로 차단(error 1010)할 수 있어 브라우저 UA 사용.
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
        async with aiohttp.ClientSession(headers={"User-Agent": ua}) as sess:
            data = {
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self._redirect_uri,
                "code": code,
            }
            async with sess.post(f"{self.base_url}/oauth/access_token", data=data) as r:
                tok = await r.json()
            access = tok.get("access_token")
            if not access:
                raise RuntimeError("no access_token")
            async with sess.get(
                f"{self.base_url}/api/v4/users/me",
                headers={"Authorization": f"Bearer {access}"},
            ) as r2:
                me = await r2.json()
        return me.get("username", ""), me.get("email", ""), access

    @staticmethod
    def _success_page(username: str, token: str) -> str:
        return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Qonvo Login</title>
<style>body{{font-family:sans-serif;background:#1e1e1e;color:#ddd;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
.box{{background:#2d2d2d;padding:32px;border-radius:12px;max-width:420px}}
code{{display:block;background:#000;color:#6cf;padding:12px;border-radius:6px;
word-break:break-all;margin:12px 0;font-size:14px}}
.u{{color:#9f9}}</style></head>
<body><div class="box">
<h2>로그인 성공</h2>
<p>아래 정보를 Qonvo의 <b>Connect to Server</b> 창에 입력하세요.</p>
<p>Username: <span class="u">{username}</span></p>
<p>Password (1회용 토큰, 10분 유효):</p>
<code>{token}</code>
</div></body></html>"""
