"""逐字稿資料結構，以及 WebVTT / SRT / JSON3 的解析。

YouTube 自動字幕（auto-generated CC）是「滾動視窗」格式：同一句話會在連續
數個 cue 裡重複出現、逐字長出來，並夾帶 <00:00:01.000><c> 字</c> 這類
inline timing tag。直接串接會得到大量重複，所以這裡做去重。
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

_TS = re.compile(
    r"(?P<h>\d{1,3}):(?P<m>\d{2}):(?P<s>\d{2})[.,](?P<ms>\d{1,3})"
    r"|(?P<m2>\d{1,3}):(?P<s2>\d{2})[.,](?P<ms2>\d{1,3})"
)
_CUE_ARROW = re.compile(r"-->")
_TAG = re.compile(r"<[^>]*>")
_WS = re.compile(r"[ \t ]+")


@dataclass
class Segment:
    start: float
    end: float
    text: str

    @property
    def stamp(self) -> str:
        return format_stamp(self.start)


@dataclass
class Transcript:
    segments: list[Segment] = field(default_factory=list)
    source: str = "unknown"  # manual_cc / auto_cc / asr
    lang: str = ""
    notes: list[str] = field(default_factory=list)
    proofread: bool = False  # 是否真的跑過 LLM 校對（補標點、修同音字）

    @property
    def text(self) -> str:
        return "\n".join(s.text for s in self.segments)

    @property
    def char_count(self) -> int:
        return sum(len(s.text) for s in self.segments)

    @property
    def duration(self) -> float:
        return self.segments[-1].end if self.segments else 0.0

    def timestamped(self, every: float = 0.0) -> str:
        """帶時間碼的逐字稿。every>0 時只在跨過該秒數間隔後才標一次時間碼。"""
        lines: list[str] = []
        last = -1e9
        for s in self.segments:
            if every <= 0 or s.start - last >= every:
                lines.append(f"[{s.stamp}] {s.text}")
                last = s.start
            else:
                lines.append(s.text)
        return "\n".join(lines)

    def window(self, start: float, end: float) -> list[Segment]:
        return [s for s in self.segments if s.start >= start and s.start < end]


def format_stamp(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_stamp(raw: str) -> float:
    """把 '1:02:03' / '2:03' / '123' 轉成秒。"""
    raw = raw.strip().lstrip("[(").rstrip(")]")
    if not raw:
        return 0.0
    parts = raw.replace(",", ".").split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return 0.0
    total = 0.0
    for n in nums:
        total = total * 60 + n
    return total


def _ts_to_seconds(m: re.Match[str]) -> float:
    if m.group("h") is not None:
        ms = m.group("ms").ljust(3, "0")
        return int(m["h"]) * 3600 + int(m["m"]) * 60 + int(m["s"]) + int(ms) / 1000
    ms = m.group("ms2").ljust(3, "0")
    return int(m["m2"]) * 60 + int(m["s2"]) + int(ms) / 1000


def _clean_line(line: str) -> str:
    line = _TAG.sub("", line)
    line = html.unescape(line)
    line = line.replace("​", "").replace("&nbsp;", " ")
    line = _WS.sub(" ", line)
    return line.strip()


def _dedupe(raw_lines: Sequence[tuple[float, float, str]]) -> list[Segment]:
    """去掉 YouTube 滾動字幕造成的重複。

    規則：與上一行相同 -> 丟掉；是上一行的延伸 -> 取代上一行；
    是上一行的前綴（較短的重複）-> 丟掉。
    """
    out: list[Segment] = []
    for start, end, text in raw_lines:
        if not text:
            continue
        if not out:
            out.append(Segment(start, end, text))
            continue
        prev = out[-1]
        if text == prev.text:
            prev.end = max(prev.end, end)
            continue
        if text.startswith(prev.text):
            prev.text = text
            prev.end = max(prev.end, end)
            continue
        if prev.text.startswith(text):
            prev.end = max(prev.end, end)
            continue
        out.append(Segment(start, end, text))
    return out


def parse_vtt(content: str) -> list[Segment]:
    """解析 WebVTT 或 SRT（兩者 cue 結構夠接近，可共用）。"""
    raw: list[tuple[float, float, str]] = []
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not _CUE_ARROW.search(line):
            i += 1
            continue
        stamps = list(_TS.finditer(line))
        if len(stamps) < 2:
            i += 1
            continue
        start = _ts_to_seconds(stamps[0])
        end = _ts_to_seconds(stamps[1])
        i += 1
        body: list[str] = []
        while i < n and lines[i].strip() and not _CUE_ARROW.search(lines[i]):
            body.append(lines[i])
            i += 1
        for b in body:
            cleaned = _clean_line(b)
            if cleaned:
                raw.append((start, end, cleaned))
    return _dedupe(raw)


def parse_json3(content: str) -> list[Segment]:
    """解析 YouTube 的 json3 字幕格式（yt-dlp --sub-format json3）。"""
    data = json.loads(content)
    raw: list[tuple[float, float, str]] = []
    for event in data.get("events", []):
        segs = event.get("segs")
        if not segs:
            continue
        start = event.get("tStartMs", 0) / 1000.0
        end = start + event.get("dDurationMs", 0) / 1000.0
        text = _clean_line("".join(s.get("utf8", "") for s in segs))
        if text:
            raw.append((start, end, text))
    return _dedupe(raw)


def parse_subtitle_file(path, content: str | None = None) -> list[Segment]:
    import pathlib

    p = pathlib.Path(path)
    if content is None:
        content = p.read_text(encoding="utf-8", errors="replace")
    if p.suffix.lower() == ".json3" or content.lstrip().startswith("{"):
        try:
            return parse_json3(content)
        except (json.JSONDecodeError, KeyError):
            pass
    return parse_vtt(content)


def merge_segments(
    segments: Iterable[Segment],
    max_chars: int = 120,
    max_gap: float = 2.0,
) -> list[Segment]:
    """把破碎的短句合併成較易讀的句子，方便送進模型與人眼校對。"""
    enders = "。！？!?…」』）)"
    out: list[Segment] = []
    for seg in segments:
        if not out:
            out.append(Segment(seg.start, seg.end, seg.text))
            continue
        cur = out[-1]
        gap = seg.start - cur.end
        too_long = len(cur.text) + len(seg.text) > max_chars
        closed = cur.text.endswith(tuple(enders))
        if closed or too_long or gap > max_gap:
            out.append(Segment(seg.start, seg.end, seg.text))
        else:
            joiner = "" if _is_cjk_edge(cur.text, seg.text) else " "
            cur.text = f"{cur.text}{joiner}{seg.text}"
            cur.end = seg.end
    return out


def _is_cjk_edge(left: str, right: str) -> bool:
    def cjk(ch: str) -> bool:
        return "㐀" <= ch <= "鿿" or "＀" <= ch <= "￯" or "　" <= ch <= "〿"

    return bool(left) and bool(right) and cjk(left[-1]) and cjk(right[0])
