"""yt-dlp 包裝：抓影片資訊、CC 字幕、音訊。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

# 字幕語言偏好：繁中優先，再簡中，最後英文
DEFAULT_SUB_LANGS = (
    "zh-Hant", "zh-TW", "zh-Hant-TW", "zh-HK", "zh-Hant-HK",
    "zh", "zh-Hans", "zh-CN", "zh-SG",
    "en", "en-US", "en-GB",
)

SUB_FORMAT_PREF = "json3/vtt/srv3/srt/best"


class YtDlpError(RuntimeError):
    pass


@dataclass
class VideoInfo:
    video_id: str
    title: str
    uploader: str
    duration: float
    upload_date: str
    description: str
    webpage_url: str
    chapters: list[dict]
    manual_subs: dict[str, list]
    auto_subs: dict[str, list]
    raw: dict

    @property
    def duration_text(self) -> str:
        from ytreel.transcript import format_stamp

        return format_stamp(self.duration) if self.duration else "未知"


def ytdlp_command() -> list[str]:
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    try:
        import yt_dlp  # noqa: F401

        return [sys.executable, "-m", "yt_dlp"]
    except ImportError as exc:
        raise YtDlpError(
            "找不到 yt-dlp。請安裝：pip install yt-dlp（或 uv tool install yt-dlp）"
        ) from exc


def _run(args: Sequence[str], *, timeout: float = 900) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    return proc


def probe(url: str, extra_args: Sequence[str] = ()) -> VideoInfo:
    """取得影片 metadata 與可用字幕清單。"""
    args = [*ytdlp_command(), "-J", "--no-warnings", "--skip-download", *extra_args, url]
    proc = _run(args)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise YtDlpError(_explain(proc.stderr or proc.stdout))
    data = json.loads(proc.stdout)
    if data.get("_type") == "playlist":
        entries = [e for e in data.get("entries", []) if e]
        if not entries:
            raise YtDlpError("這個連結是空的播放清單")
        data = entries[0]
    return VideoInfo(
        video_id=data.get("id", ""),
        title=data.get("title") or "(無標題)",
        uploader=data.get("uploader") or data.get("channel") or "",
        duration=float(data.get("duration") or 0),
        upload_date=data.get("upload_date") or "",
        description=data.get("description") or "",
        webpage_url=data.get("webpage_url") or url,
        chapters=list(data.get("chapters") or []),
        manual_subs=dict(data.get("subtitles") or {}),
        auto_subs=dict(data.get("automatic_captions") or {}),
        raw=data,
    )


def pick_subtitle_lang(
    info: VideoInfo, prefs: Sequence[str] = DEFAULT_SUB_LANGS
) -> tuple[str, str] | None:
    """挑一個字幕語言。回傳 (lang, kind)，kind 是 manual_cc 或 auto_cc。

    人工字幕一律優先於自動字幕，就算語言偏好順序較後面也一樣 —— 人工字幕
    沒有語音辨識錯誤，品質差距比語言差距大得多。
    """
    for table, kind in ((info.manual_subs, "manual_cc"), (info.auto_subs, "auto_cc")):
        if not table:
            continue
        available = {k.lower(): k for k in table}
        for want in prefs:
            if (hit := available.get(want.lower())) is not None:
                return hit, kind
        # 偏好清單都沒中，退而取第一個中文，再退而取任何一個
        for key in table:
            if key.lower().startswith("zh"):
                return key, kind
        return next(iter(table)), kind
    return None


def download_subtitle(
    url: str,
    lang: str,
    kind: str,
    outdir: Path,
    extra_args: Sequence[str] = (),
) -> Path:
    """下載指定語言字幕，回傳字幕檔路徑。"""
    outdir.mkdir(parents=True, exist_ok=True)
    flag = "--write-subs" if kind == "manual_cc" else "--write-auto-subs"
    args = [
        *ytdlp_command(),
        "--no-warnings",
        "--skip-download",
        flag,
        "--sub-langs", lang,
        "--sub-format", SUB_FORMAT_PREF,
        "-o", str(outdir / "%(id)s.%(ext)s"),
        *extra_args,
        url,
    ]
    proc = _run(args)
    found = _newest_subtitle(outdir)
    if found is None:
        raise YtDlpError(
            "字幕下載失敗：" + _explain(proc.stderr or proc.stdout or "yt-dlp 沒有產出字幕檔")
        )
    return found


def _newest_subtitle(outdir: Path) -> Path | None:
    cands = [
        p
        for p in outdir.iterdir()
        if p.is_file() and p.suffix.lower() in (".vtt", ".srt", ".json3", ".srv3", ".ttml")
    ]
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_mtime)


def download_audio(
    url: str,
    outdir: Path,
    extra_args: Sequence[str] = (),
    sample_rate: int = 16000,
) -> Path:
    """下載音訊並轉成 16kHz 單聲道 wav（Whisper 要的格式）。"""
    outdir.mkdir(parents=True, exist_ok=True)
    args = [
        *ytdlp_command(),
        "--no-warnings",
        "-f", "bestaudio/best",
        "-x",
        "--audio-format", "wav",
        "--postprocessor-args", f"ExtractAudio:-ar {sample_rate} -ac 1",
        "-o", str(outdir / "%(id)s.%(ext)s"),
        *extra_args,
        url,
    ]
    proc = _run(args, timeout=3600)
    wavs = sorted(outdir.glob("*.wav"), key=lambda p: p.stat().st_mtime)
    if not wavs:
        raise YtDlpError(
            "音訊下載失敗：" + _explain(proc.stderr or proc.stdout or "yt-dlp 沒有產出 wav")
        )
    return wavs[-1]


def _explain(stderr: str) -> str:
    """把 yt-dlp 的常見錯誤翻成可行動的說明。"""
    s = (stderr or "").strip()
    low = s.lower()
    tail = s.splitlines()[-1] if s else "（無錯誤輸出）"
    if "tunnel connection failed" in low or "proxyerror" in low or "403 forbidden" in low and "proxy" in low:
        return (
            f"{tail}\n"
            "→ 連不到 YouTube。這通常是執行環境的網路政策把 youtube.com 擋掉了，"
            "請在允許連外的機器上執行，或把 youtube.com / googlevideo.com 加進允許清單。"
        )
    if "sign in to confirm" in low or "bot" in low and "cookies" in low:
        return (
            f"{tail}\n"
            "→ YouTube 要求驗證。請加上 --cookies-from-browser chrome"
            "（或 --cookies cookies.txt）再試。"
        )
    if "video unavailable" in low or "private video" in low:
        return f"{tail}\n→ 影片不存在、已下架或為私人影片。"
    if "members-only" in low or "join this channel" in low:
        return f"{tail}\n→ 會員限定影片，需要用登入過的 cookies。"
    return tail
