"""merri(Mattermost) REST 클라이언트 — People 패널의 백엔드.

qonvo 프로필(merri access token + merri_url)로 Mattermost API 를 직접 호출한다.
계정/연락처/온라인상태/DM 채팅을 merri 가 이미 제공하므로 얇게 감싸기만 한다.

- Cloudflare 가 Python UA 를 봇으로 차단(error 1010)하므로 브라우저 UA 를 보낸다.
- 의존성 없이 stdlib urllib 사용(앱 다른 곳과 동일). 블로킹이므로 호출측은
  QThread/워커에서 쓰는 것을 권장(People 패널이 폴링 스레드로 감쌈).

연락처는 Mattermost 에 친구 개념이 없어, qonvo 가 로컬(settings `qonvo_contacts`,
merri user_id 목록)로 관리한다. 검색으로 추가 → DM 시작 시 자동 등록.
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import List, Optional

from v.settings import get_setting, set_setting

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


# ---- 로컬 연락처(merri user_id 목록) -----------------------------------
def get_contacts() -> List[str]:
    v = get_setting("qonvo_contacts", [])
    return [str(x) for x in v] if isinstance(v, list) else []


def add_contact(user_id: str) -> None:
    ids = get_contacts()
    if user_id and user_id not in ids:
        ids.append(user_id)
        set_setting("qonvo_contacts", ids)


def remove_contact(user_id: str) -> None:
    set_setting("qonvo_contacts", [i for i in get_contacts() if i != user_id])


class MerriError(Exception):
    pass


class MerriClient:
    """Mattermost API v4 얇은 래퍼(동기). 실패 시 MerriError."""

    def __init__(self, merri_url: str, token: str, timeout: float = 12.0):
        self.base = (merri_url or "").rstrip("/")
        self.token = token
        self.timeout = timeout

    @classmethod
    def from_profile(cls) -> Optional["MerriClient"]:
        """저장된 qonvo 프로필로 클라이언트를 만든다. merri_url/token 없으면 None."""
        from .profile import get_profile
        p = get_profile()
        if not p or not p.get("token") or not p.get("merri_url"):
            return None
        return cls(p["merri_url"], p["token"])

    # ---- 저수준 ---------------------------------------------------------
    def _req(self, method: str, path: str, body=None):
        url = f"{self.base}/api/v4{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "User-Agent": _UA,
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raise MerriError(f"HTTP {e.code} {path}")
        except Exception as e:
            raise MerriError(str(e))

    # ---- 사용자/상태 ----------------------------------------------------
    def me(self) -> dict:
        return self._req("GET", "/users/me")

    def search_users(self, term: str, limit: int = 20) -> List[dict]:
        if not term.strip():
            return []
        res = self._req("POST", "/users/search",
                        {"term": term.strip(), "limit": limit, "allow_inactive": False})
        return res if isinstance(res, list) else []

    def users_by_ids(self, ids: List[str]) -> List[dict]:
        if not ids:
            return []
        res = self._req("POST", "/users/ids", ids)
        return res if isinstance(res, list) else []

    def statuses(self, ids: List[str]) -> dict:
        """user_id -> status(online/away/dnd/offline) 맵."""
        if not ids:
            return {}
        res = self._req("POST", "/users/status/ids", ids)
        out = {}
        if isinstance(res, list):
            for s in res:
                out[s.get("user_id", "")] = s.get("status", "offline")
        return out

    # ---- DM 채널/메시지 -------------------------------------------------
    def direct_channel(self, my_id: str, other_id: str) -> dict:
        """두 사용자 간 DM 채널을 얻거나 생성한다."""
        return self._req("POST", "/channels/direct", [my_id, other_id])

    def get_posts(self, channel_id: str, per_page: int = 30) -> List[dict]:
        """채널 메시지를 시간순(오래된→최신)으로 반환한다."""
        res = self._req("GET", f"/channels/{channel_id}/posts?per_page={per_page}")
        order = res.get("order", []) if isinstance(res, dict) else []
        posts = res.get("posts", {}) if isinstance(res, dict) else {}
        # order 는 최신→오래된 이므로 뒤집어 반환
        return [posts[pid] for pid in reversed(order) if pid in posts]

    def create_post(self, channel_id: str, message: str, file_ids=None, props=None,
                    root_id: str = "") -> dict:
        body = {"channel_id": channel_id, "message": message}
        if file_ids:
            body["file_ids"] = file_ids
        if props:
            body["props"] = props
        if root_id:
            body["root_id"] = root_id   # 답글(스레드)
        return self._req("POST", "/posts", body)

    # ---- 반응(이모지) --------------------------------------------------
    def add_reaction(self, user_id: str, post_id: str, emoji_name: str) -> dict:
        return self._req("POST", "/reactions", {
            "user_id": user_id, "post_id": post_id, "emoji_name": emoji_name})

    def remove_reaction(self, user_id: str, post_id: str, emoji_name: str) -> dict:
        return self._req("DELETE", f"/users/{user_id}/posts/{post_id}/reactions/{emoji_name}")

    def upload_file(self, channel_id: str, filename: str, data: bytes) -> str:
        """파일 바이트를 채널에 업로드하고 file_id 를 반환한다(실패 시 "")."""
        from urllib.parse import quote
        if not data:
            return ""
        url = f"{self.base}/api/v4/files?channel_id={channel_id}&filename={quote(filename)}"
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "Authorization": f"Bearer {self.token}", "User-Agent": _UA,
            "Content-Type": "application/octet-stream"})
        try:
            with urllib.request.urlopen(req, timeout=max(self.timeout, 30)) as r:
                res = json.loads(r.read().decode("utf-8"))
            infos = res.get("file_infos", [])
            return infos[0].get("id", "") if infos else ""
        except Exception:
            return ""

    # ---- 이미지 바이트(아바타/첨부) ------------------------------------
    def _raw_get(self, path: str) -> bytes:
        url = f"{self.base}/api/v4{path}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.token}", "User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read()
        except Exception:
            return b""

    def get_image(self, user_id: str) -> bytes:
        """사용자 프로필 이미지 바이트. 실패 시 빈 bytes."""
        return self._raw_get(f"/users/{user_id}/image")

    def get_file(self, file_id: str) -> bytes:
        """메시지 첨부 파일(이미지 등) 바이트. 실패 시 빈 bytes."""
        return self._raw_get(f"/files/{file_id}")
