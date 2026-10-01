"""編排：逐字稿 → 摘要 / 重點 / Reel 腳本。

短影片直接一次呼叫；長影片先 map（分段筆記）再 reduce（寫摘要與腳本），
reduce 時仍附上逐字稿的頭尾節錄，讓模型抓得到講者真正的用語與語氣。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from ytreel.llm import NoLLMBackend
from ytreel.models import Analysis, SectionNotesList
from ytreel.transcript import Segment, Transcript
from ytreel.youtube import VideoInfo

# 直接整份送進模型的字數上限。超過才改走 map-reduce。
DEFAULT_MAX_DIRECT_CHARS = 250_000
DEFAULT_MAP_BLOCK_CHARS = 30_000
REDUCE_EXCERPT_CHARS = 20_000


@dataclass
class AnalyzeReport:
    mode: str = "direct"
    map_blocks: int = 0
    warnings: list[str] = field(default_factory=list)


def _blocks(segments: Sequence[Segment], block_chars: int) -> list[list[Segment]]:
    out: list[list[Segment]] = []
    cur: list[Segment] = []
    size = 0
    for s in segments:
        if cur and size + len(s.text) > block_chars:
            out.append(cur)
            cur, size = [], 0
        cur.append(s)
        size += len(s.text)
    if cur:
        out.append(cur)
    return out


def _excerpt(transcript: Transcript, chars: int) -> str:
    body = transcript.timestamped(every=0)
    if len(body) <= chars * 2:
        return body
    head, tail = body[:chars], body[-chars:]
    return f"{head}\n\n……（中段省略，內容見上方分段筆記）……\n\n{tail}"


def analyze(
    transcript: Transcript,
    info: VideoInfo,
    llm,
    *,
    reels: int = 4,
    seconds: int = 40,
    tone: str = "",
    audience: str = "",
    glossary: Sequence[str] = (),
    max_direct_chars: int = DEFAULT_MAX_DIRECT_CHARS,
    map_block_chars: int = DEFAULT_MAP_BLOCK_CHARS,
    max_output_tokens: int = 32_000,
    report: AnalyzeReport | None = None,
    progress=None,
) -> Analysis:
    from ytreel.prompts import ANALYZE_SYSTEM, MAP_SYSTEM, analyze_prompt, map_prompt

    report = report or AnalyzeReport()
    if not transcript.segments:
        raise ValueError("逐字稿是空的，沒有東西可以分析")

    section_notes = ""
    if transcript.char_count > max_direct_chars:
        report.mode = "map-reduce"
        blocks = _blocks(transcript.segments, map_block_chars)
        report.map_blocks = len(blocks)
        notes: list[str] = []
        for i, block in enumerate(blocks):
            if progress:
                progress("map", i + 1, len(blocks))
            body = "\n".join(f"[{s.stamp}] {s.text}" for s in block)
            try:
                result = llm.complete_json(
                    system=MAP_SYSTEM,
                    prompt=map_prompt(body, i + 1, len(blocks)),
                    output_format=SectionNotesList,
                    max_tokens=8_000,
                    effort="medium",
                )
            except NoLLMBackend.Skipped:
                raise  # --llm none：交給上層輸出提示詞包
            except Exception as exc:  # noqa: BLE001 - 單一區塊失敗不該讓整支中斷
                report.warnings.append(f"第 {i + 1}/{len(blocks)} 區塊分段筆記失敗：{exc}")
                continue
            for sec in result.sections:
                notes.append(f"## {sec.section_title}（{sec.timestamp_range}）")
                notes += [f"- {p}" for p in sec.points]
                notes += [f"> {q}" for q in sec.quotes]
                notes.append("")
        section_notes = "\n".join(notes).strip()
        if not section_notes:
            report.warnings.append("所有分段筆記都失敗，改用逐字稿節錄直接分析")
        body = _excerpt(transcript, REDUCE_EXCERPT_CHARS)
    else:
        body = transcript.timestamped(every=0)

    if progress:
        progress("analyze", 1, 1)

    prompt = analyze_prompt(
        title=info.title,
        uploader=info.uploader,
        duration_text=info.duration_text,
        url=info.webpage_url,
        transcript_body=body,
        transcript_source=transcript.source,
        transcript_proofread=transcript.proofread,
        reels=reels,
        seconds=seconds,
        tone=tone,
        audience=audience,
        glossary=glossary,
        chapters=info.chapters,
        section_notes=section_notes,
    )
    analysis = llm.complete_json(
        system=ANALYZE_SYSTEM,
        prompt=prompt,
        output_format=Analysis,
        max_tokens=max_output_tokens,
        effort="high",
        cache=len(prompt) > 8_000,
    )

    _postcheck(analysis, transcript, reels, report)
    return analysis


def _postcheck(
    analysis: Analysis, transcript: Transcript, wanted: int, report: AnalyzeReport
) -> None:
    """模型回來後的健檢。這些是提醒，不是錯誤 —— 不丟掉已經產出的結果。"""
    from ytreel.transcript import parse_stamp

    got = len(analysis.reels)
    if got != wanted:
        report.warnings.append(f"要求 {wanted} 支 Reel，模型給了 {got} 支")
    if got < 3:
        report.warnings.append("Reel 少於 3 支，建議加大 --max-output-tokens 後重跑")

    theses = [r.thesis.strip() for r in analysis.reels]
    if len(set(theses)) != len(theses):
        report.warnings.append("有 Reel 的論點重複")

    limit = transcript.duration + 60  # 容許一點誤差
    for i, reel in enumerate(analysis.reels, 1):
        for ts in reel.source_timestamps:
            if limit > 60 and parse_stamp(ts) > limit:
                report.warnings.append(
                    f"Reel {i} 的時間碼 {ts} 超出影片長度（{transcript.duration:.0f} 秒），可能是模型編的"
                )
        chars = len(reel.full_script)
        est = max(1, reel.est_seconds)
        actual = chars / 4  # 中文口播約每秒 4 字
        if not (0.6 <= actual / est <= 1.6):
            report.warnings.append(
                f"Reel {i} 口播稿 {chars} 字（約 {actual:.0f} 秒）"
                f"與預估 {est} 秒落差較大"
            )
