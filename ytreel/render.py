"""把分析結果寫成可直接用的 Markdown 報告。"""

from __future__ import annotations

from ytreel.analyze import AnalyzeReport
from ytreel.correct import CleanReport
from ytreel.models import Analysis, ReelScript
from ytreel.transcript import Transcript, parse_stamp
from ytreel.urls import timestamp_url
from ytreel.youtube import VideoInfo

_SOURCE_LABEL = {"manual_cc": "人工 CC 字幕"}
_MACHINE_LABEL = {"auto_cc": "YouTube 自動字幕", "asr": "語音辨識 Whisper"}


def source_label(transcript) -> str:
    base = _MACHINE_LABEL.get(transcript.source)
    if base:
        return base + ("（已修錯字、補標點）" if transcript.proofread else "（僅規則修正，未經模型校對）")
    return _SOURCE_LABEL.get(transcript.source, transcript.source)


def _cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", "<br>").strip()


def _ts_link(video_id: str, stamp: str) -> str:
    stamp = (stamp or "").strip()
    if not stamp:
        return ""
    secs = parse_stamp(stamp)
    if secs <= 0 and stamp not in ("0:00", "00:00", "0:00:00"):
        return f"`{stamp}`"
    return f"[`{stamp}`]({timestamp_url(video_id, secs)})"


def render_reel(reel: ReelScript, index: int, video_id: str) -> str:
    chars = len(reel.full_script)
    actual = chars / 4
    stamps = "、".join(_ts_link(video_id, t) for t in reel.source_timestamps) or "—"

    out = [
        f"### Reel {index}｜{reel.title}",
        "",
        f"**論點**　{reel.thesis}",
        "",
        f"**為什麼現在要看**　{reel.why_now}",
        "",
        f"**長度**　預估 {reel.est_seconds} 秒｜口播稿 {chars} 字（念起來約 {actual:.0f} 秒）",
        "",
        f"**取材**　{stamps}",
        "",
        "**鉤子（前 3 秒）**",
        "",
        f"> {reel.hook}",
        "",
        "**分鏡**",
        "",
        "| 段落 | 口播 | 畫面 | 字卡 |",
        "| --- | --- | --- | --- |",
    ]
    for beat in reel.beats:
        out.append(
            f"| {_cell(beat.label)} | {_cell(beat.voiceover)} "
            f"| {_cell(beat.visual)} | {_cell(beat.on_screen)} |"
        )
    out += [
        "",
        "**完整口播稿**（可直接念）",
        "",
        "```text",
        reel.full_script.strip(),
        "```",
        "",
        f"**結尾 CTA**　{reel.cta}",
        "",
        "**IG 貼文文案**",
        "",
        reel.caption.strip(),
        "",
        " ".join(h if h.startswith("#") else f"#{h}" for h in reel.hashtags),
        "",
    ]
    return "\n".join(out)


def render_report(
    analysis: Analysis,
    info: VideoInfo,
    transcript: Transcript,
    *,
    model_label: str = "",
    clean: CleanReport | None = None,
    analyzed: AnalyzeReport | None = None,
) -> str:
    vid = info.video_id
    source = source_label(transcript)
    if transcript.notes:
        source += "（" + "；".join(transcript.notes) + "）"

    out = [
        f"# {info.title}",
        "",
        "| 項目 | 內容 |",
        "| --- | --- |",
        f"| 頻道 | {_cell(info.uploader) or '未知'} |",
        f"| 長度 | {info.duration_text} |",
        f"| 連結 | {info.webpage_url} |",
        f"| 逐字稿來源 | {_cell(source)} |",
        f"| 逐字稿字數 | {transcript.char_count:,} 字 |",
    ]
    if model_label:
        out.append(f"| 分析模型 | {_cell(model_label)} |")
    if analyzed and analyzed.mode != "direct":
        out.append(f"| 分析方式 | {analyzed.mode}（{analyzed.map_blocks} 個區塊）|")

    out += [
        "",
        "## 一句話總結",
        "",
        f"> {analysis.one_liner}",
        "",
        f"**適合誰看**　{analysis.audience}",
        "",
        "## 摘要",
        "",
        analysis.summary.strip(),
        "",
        "## 重點整理",
        "",
    ]
    for i, kp in enumerate(analysis.key_points, 1):
        link = _ts_link(vid, kp.timestamp)
        out += [f"**{i}. {kp.title}**　{link}", "", kp.detail.strip(), ""]

    if analysis.quotes:
        out += ["## 金句", ""]
        for q in analysis.quotes:
            out += [f"> {q.text.strip()}", "", f"　— {_ts_link(vid, q.timestamp)}　{q.why}", ""]

    out += [
        "## IG Reel 口播稿",
        "",
        f"共 {len(analysis.reels)} 支，每支一個獨立論點。",
        "",
    ]
    for i, reel in enumerate(analysis.reels, 1):
        out.append(render_reel(reel, i, vid))
        out.append("---")
        out.append("")

    notes = _pipeline_notes(clean, analyzed)
    if notes:
        out += ["## 處理紀錄", ""] + notes + [""]

    return "\n".join(out).rstrip() + "\n"


def _pipeline_notes(
    clean: CleanReport | None, analyzed: AnalyzeReport | None
) -> list[str]:
    notes: list[str] = []
    if clean:
        notes.append(f"- 逐字稿清理：{clean.summary()}")
        notes += [f"- ⚠️ {w}" for w in clean.warnings]
    if analyzed:
        notes += [f"- ⚠️ {w}" for w in analyzed.warnings]
    return notes


def render_scripts_only(analysis: Analysis) -> str:
    """只有口播稿的純文字版，方便直接丟進提詞機。"""
    out: list[str] = []
    for i, reel in enumerate(analysis.reels, 1):
        out += [
            f"=== Reel {i}｜{reel.title} ===",
            f"論點：{reel.thesis}",
            f"預估 {reel.est_seconds} 秒 / {len(reel.full_script)} 字",
            "",
            reel.full_script.strip(),
            "",
            f"CTA：{reel.cta}",
            "",
            "",
        ]
    return "\n".join(out)


def render_transcript_file(transcript: Transcript, info: VideoInfo) -> str:
    head = [
        f"# {info.title} — 逐字稿",
        "",
        f"來源：{source_label(transcript)}",
        f"連結：{info.webpage_url}",
        "",
    ]
    return "\n".join(head) + transcript.timestamped(every=0) + "\n"
