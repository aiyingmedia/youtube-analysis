"""YouTube 連結解析。"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

# 11 碼的 YouTube video id
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

_PATH_PREFIXES = ("/embed/", "/v/", "/shorts/", "/live/")


class InvalidYouTubeURL(ValueError):
    """無法從輸入解析出 YouTube video id。"""


def extract_video_id(raw: str) -> str:
    """從各種 YouTube 連結格式取出 video id。

    支援 watch?v=、youtu.be/、/shorts/、/embed/、/live/、/v/，
    也接受直接傳入 11 碼 id。
    """
    s = (raw or "").strip().strip("<>").strip()
    if not s:
        raise InvalidYouTubeURL("連結是空的")

    if _ID_RE.match(s):
        return s

    if "://" not in s:
        s = "https://" + s

    u = urlparse(s)
    host = (u.hostname or "").lower().removeprefix("www.").removeprefix("m.")

    if host in ("youtu.be", "youtube.be"):
        candidate = u.path.lstrip("/").split("/")[0]
        if _ID_RE.match(candidate):
            return candidate

    if host.endswith("youtube.com") or host.endswith("youtube-nocookie.com"):
        qs = parse_qs(u.query)
        for key in ("v", "video_id"):
            for value in qs.get(key, []):
                if _ID_RE.match(value):
                    return value
        for prefix in _PATH_PREFIXES:
            if u.path.startswith(prefix):
                candidate = u.path[len(prefix) :].split("/")[0]
                if _ID_RE.match(candidate):
                    return candidate

    # 最後手段：在字串裡找一段看起來像 id 的片段（例如含追蹤參數的分享連結）
    m = re.search(r"(?:v=|/)([A-Za-z0-9_-]{11})(?=[?&/#]|$)", s)
    if m:
        return m.group(1)

    raise InvalidYouTubeURL(f"看不出這是 YouTube 連結：{raw!r}")


def canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def timestamp_url(video_id: str, seconds: float) -> str:
    return f"{canonical_url(video_id)}&t={int(seconds)}s"
