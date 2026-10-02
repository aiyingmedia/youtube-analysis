"""yt-dlp 包裝：抓影片資訊、CC 字幕、音訊。"""

from __future__ import annotations

import json
import re
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
    """優先用跟 ytreel 同一個 Python 環境裡的 yt-dlp。

    PATH 上的 yt-dlp 可能是另外裝的舊版（例如 brew），它讀不到這個環境裡的
    yt-dlp-ejs，太舊的話連 --js-runtimes 都不認得。用同環境的模組才能確保
    pyproject 指定的版本與解題腳本真的有被用到。
    """
    try:
        import yt_dlp  # noqa: F401

        return [sys.executable, "-m", "yt_dlp"]
    except ImportError:
        pass
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    raise YtDlpError("找不到 yt-dlp。請安裝：pip install 'yt-dlp[default]'")


# yt-dlp 支援的 JS 執行環境。deno 是它的預設，其他的要明確啟用。
_JS_RUNTIMES = ("deno", "node", "bun")


def js_runtime_args() -> list[str]:
    """讓 yt-dlp 用得到 JavaScript 執行環境。

    YouTube 的播放驗證要跑 JS 才解得開。yt-dlp 找不到執行環境時會改用
    降級模式：格式會缺、串流網址可能回 403，連字幕清單都可能被漏掉。
    yt-dlp 預設只啟用 deno，所以機器上只有 node 或 bun 時要明確指定。
    """
    if shutil.which("deno"):
        return []
    for name in _JS_RUNTIMES[1:]:
        if shutil.which(name):
            return ["--js-runtimes", name]
    return []


def has_js_runtime() -> bool:
    return any(shutil.which(name) for name in _JS_RUNTIMES)


def _base() -> list[str]:
    return [*ytdlp_command(), *js_runtime_args()]


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
    args = [*_base(), "-J", "--skip-download", *extra_args, url]
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
        *_base(),
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
        *_base(),
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


_BOT_REMEDIES = (
    "  在這種狀態下 yt-dlp 也拿不到字幕清單，所以「沒有找到字幕」不一定是真的沒有。\n"
    "  解法（擇一）：\n"
    "  1. 換到一般家用網路執行（雲端主機、資料中心、VPN 的 IP 最常被擋）\n"
    "  2. 在 YouTube 影片說明欄點「顯示轉錄稿」，複製存成 .txt，用 --subtitle-file 匯入\n"
    "  3. 加上 --cookies-from-browser chrome。這等於把 YouTube 登入狀態交給 yt-dlp，"
    "建議用分身帳號，YouTube 可能把用來下載的帳號標記為異常"
)


def _headline(line: str) -> str:
    # yt-dlp 的錯誤訊息常附帶「請回報 issue」，但這類錯誤幾乎都不是 yt-dlp 的 bug
    return re.sub(r";?\s*please report this issue.*$", "", line, flags=re.I).strip()


def _explain(stderr: str) -> str:
    """把 yt-dlp 的錯誤翻成可行動的說明。

    判斷原因只看 ERROR 行：警告裡常有不相干的連線失敗（例如被導到 Google
    驗證頁時的 tunnel 403），拿整段 stderr 比對會誤判成網路政策問題。
    """
    s = (stderr or "").strip()
    lines = s.splitlines()
    errors = [ln.strip() for ln in lines if ln.lstrip().startswith("ERROR")]
    headline = _headline(errors[-1]) if errors else (lines[-1] if lines else "（無錯誤輸出）")
    err = " ".join(errors).lower() if errors else s.lower()

    notes: list[str] = []
    if any(k in err for k in ("sign in to confirm", "not a bot", "page needs to be reloaded")):
        notes.append("→ YouTube 把這個網路判定成機器人流量，要求登入才給看。\n" + _BOT_REMEDIES)
    elif "unable to download video data" in err and "403" in err:
        notes.append(
            "→ YouTube 拒絕了串流下載（403）。最常見的原因是這個網路被判定成機器人流量。\n"
            + _BOT_REMEDIES
        )
    elif "tunnel connection failed" in err or "proxyerror" in err:
        notes.append(
            "→ 連不到 YouTube，是執行環境的網路政策擋掉了。允許清單要包含 "
            "*.youtube.com 與 *.googlevideo.com（要有開頭的 *. 才會比對子網域）。"
        )
    elif "video unavailable" in err or "private video" in err:
        notes.append("→ 影片不存在、已下架或為私人影片。")
    elif "members-only" in err or "join this channel" in err:
        notes.append("→ 會員限定影片，需要用登入過的 cookies。")

    # 這個只會出現在警告裡，所以看整段 stderr
    if "no supported javascript runtime" in s.lower():
        notes.append(
            "→ 找不到 JavaScript 執行環境，yt-dlp 只能用降級模式解析 YouTube（格式會缺、"
            "字幕可能被漏掉）。請安裝 deno（macOS：brew install deno）或 Node.js。"
        )
    return "\n".join([headline, *notes])
