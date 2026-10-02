"""編排邏輯與報告輸出。"""

import re

import pytest

from ytreel.analyze import (
    AnalyzeReport,
    _blocks,
    _excerpt,
    _postcheck,
    analyze,
)
from ytreel.models import (
    Analysis,
    Beat,
    KeyPoint,
    Quote,
    ReelScript,
    SectionNotes,
    SectionNotesList,
)
from ytreel.render import render_reel, render_report, render_scripts_only
from ytreel.transcript import Segment, Transcript
from ytreel.youtube import VideoInfo


def _info(duration=180.0):
    return VideoInfo(
        video_id="dQw4w9WgXcQ", title="測試影片", uploader="測試頻道",
        duration=duration, upload_date="20260101", description="",
        webpage_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        chapters=[], manual_subs={}, auto_subs={}, raw={},
    )


def _reel(thesis="一個可以被反駁的主張", script="字" * 120, est=30, stamps=("0:30",)):
    return ReelScript(
        title="標題", thesis=thesis, why_now="因為現在就在發生",
        hook="具體有張力的開場", beats=[Beat(label="0-3 秒｜鉤子", voiceover="念這句", visual="拍這個", on_screen="字卡")],
        full_script=script, cta="留言告訴我", est_seconds=est,
        source_timestamps=list(stamps), caption="貼文文案", hashtags=["#甲", "#乙"],
    )


def _analysis(reels=None):
    return Analysis(
        one_liner="一句話總結", audience="想學的人", summary="摘要內容",
        key_points=[KeyPoint(title="重點一", detail="說明", timestamp="1:05")],
        quotes=[Quote(text="金句", timestamp="2:00", why="因為有力")],
        reels=reels if reels is not None else [_reel()],
    )


def _transcript(n=30, chars=20):
    return Transcript(
        segments=[Segment(i * 5, i * 5 + 5, "字" * chars) for i in range(n)],
        source="auto_cc",
    )


# --- 分段 ----------------------------------------------------------------

def test_blocks_cover_every_segment():
    segs = [Segment(i, i + 1, "字" * 40) for i in range(25)]
    blocks = _blocks(segs, 200)
    assert sum(len(b) for b in blocks) == 25
    assert [s.text for b in blocks for s in b] == [s.text for s in segs]


def test_excerpt_keeps_head_and_tail_and_marks_the_gap():
    t = Transcript(segments=[Segment(i, i + 1, f"第{i}句") for i in range(400)])
    out = _excerpt(t, 200)
    assert "第0句" in out and "第399句" in out
    assert "中段省略" in out
    assert len(out) < len(t.timestamped(every=0))


def test_short_transcript_is_not_excerpted():
    t = Transcript(segments=[Segment(0, 1, "短")])
    assert "中段省略" not in _excerpt(t, 200)


# --- 健檢 ----------------------------------------------------------------

def test_postcheck_flags_wrong_reel_count():
    report = AnalyzeReport()
    _postcheck(_analysis([_reel()]), _transcript(), 4, report)
    assert any("模型給了 1 支" in w for w in report.warnings)


def test_postcheck_flags_duplicate_theses():
    report = AnalyzeReport()
    _postcheck(_analysis([_reel(thesis="同一句"), _reel(thesis="同一句")]), _transcript(), 2, report)
    assert any("論點重複" in w for w in report.warnings)


def test_postcheck_flags_hallucinated_timestamp():
    report = AnalyzeReport()
    # 逐字稿只有 150 秒，卻引用 99:00
    _postcheck(_analysis([_reel(stamps=("99:00",))]), _transcript(), 1, report)
    assert any("超出影片長度" in w for w in report.warnings)


def test_postcheck_accepts_timestamp_within_video():
    report = AnalyzeReport()
    _postcheck(_analysis([_reel(stamps=("1:00",))]), _transcript(), 1, report)
    assert not any("超出影片長度" in w for w in report.warnings)


def test_postcheck_flags_script_length_mismatch():
    report = AnalyzeReport()
    # 說是 30 秒，卻寫了 600 字（約 150 秒）
    _postcheck(_analysis([_reel(script="字" * 600, est=30)]), _transcript(), 1, report)
    assert any("落差較大" in w for w in report.warnings)


def test_postcheck_accepts_consistent_length():
    report = AnalyzeReport()
    _postcheck(_analysis([_reel(script="字" * 120, est=30)]), _transcript(), 1, report)
    assert not any("落差較大" in w for w in report.warnings)


# --- 編排 ----------------------------------------------------------------

class _StubLLM:
    """回傳固定結果，並記錄每次呼叫的 prompt。"""

    def __init__(self):
        self.prompts = []

    def complete_json(self, *, system, prompt, output_format, max_tokens, effort="high", cache=False):
        self.prompts.append(prompt)
        if output_format is SectionNotesList:
            return SectionNotesList(
                sections=[SectionNotes(
                    section_title="小節", timestamp_range="0:00-1:00",
                    points=["要點 [0:10]"], quotes=["原話 [0:20]"],
                )]
            )
        return _analysis([_reel()])


def test_short_transcript_goes_direct():
    llm = _StubLLM()
    report = AnalyzeReport()
    analyze(_transcript(), _info(), llm, reels=1, max_direct_chars=10_000, report=report)
    assert report.mode == "direct"
    assert len(llm.prompts) == 1


def test_long_transcript_uses_map_reduce_and_injects_notes():
    llm = _StubLLM()
    report = AnalyzeReport()
    analyze(
        _transcript(n=200, chars=50), _info(), llm,
        reels=1, max_direct_chars=1_000, map_block_chars=2_000, report=report,
    )
    assert report.mode == "map-reduce"
    assert report.map_blocks > 1
    assert len(llm.prompts) == report.map_blocks + 1
    assert "分段筆記" in llm.prompts[-1]
    assert "要點 [0:10]" in llm.prompts[-1]


def test_empty_transcript_rejected():
    with pytest.raises(ValueError, match="逐字稿是空的"):
        analyze(Transcript(segments=[]), _info(), _StubLLM())


def test_analysis_body_stamps_every_segment():
    """時間碼要夠細，模型才引用得準。"""
    llm = _StubLLM()
    analyze(_transcript(n=10), _info(), llm, reels=1, max_direct_chars=10_000)
    assert llm.prompts[0].count("[") >= 10


# --- 報告輸出 ------------------------------------------------------------

def test_report_contains_every_section():
    md = render_report(_analysis(), _info(), _transcript())
    for heading in ("# 測試影片", "## 一句話總結", "## 摘要", "## 重點整理", "## 金句", "## IG Reel 口播稿"):
        assert heading in md


def test_timestamps_become_clickable_links():
    md = render_report(_analysis(), _info(), _transcript())
    assert "[`1:05`](https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=65s)" in md


def test_reel_table_escapes_pipes_and_newlines():
    beat = Beat(label="a|b", voiceover="第一行\n第二行", visual="v", on_screen="—")
    reel = _reel()
    reel.beats = [beat]
    md = render_reel(reel, 1, "dQw4w9WgXcQ")
    table_row = [ln for ln in md.splitlines() if ln.startswith("| a")][0]
    # 內容裡的 | 必須被轉義，否則表格會多出一欄
    assert r"a\|b" in table_row
    assert len(re.findall(r"(?<!\\)\|", table_row)) == 5  # 4 欄 + 首尾
    assert "<br>" in table_row  # 換行也不能破表格


def test_report_shows_actual_script_length_next_to_estimate():
    md = render_reel(_reel(script="字" * 160, est=40), 1, "x")
    assert "160 字" in md and "預估 40 秒" in md


def test_report_records_warnings():
    from ytreel.correct import CleanReport

    clean = CleanReport(warnings=["有個問題"])
    analyzed = AnalyzeReport(warnings=["另一個問題"])
    md = render_report(_analysis(), _info(), _transcript(), clean=clean, analyzed=analyzed)
    assert "## 處理紀錄" in md and "有個問題" in md and "另一個問題" in md


def test_scripts_only_output_is_plain_text():
    txt = render_scripts_only(_analysis())
    assert "Reel 1" in txt and "字" * 10 in txt
    assert "|" not in txt and "```" not in txt


# --- 逐字稿品質要誠實標示 -----------------------------------------------

def test_prompt_warns_when_proofread_was_skipped():
    from ytreel.prompts import analyze_prompt

    kw = dict(title="t", uploader="u", duration_text="1:00", url="x",
              transcript_body="[0:00] 內容", transcript_source="auto_cc", reels=3, seconds=30)
    skipped = analyze_prompt(**kw, transcript_proofread=False)
    done = analyze_prompt(**kw, transcript_proofread=True)
    assert "沒有經過模型校對" in skipped
    assert "沒有經過模型校對" not in done
    assert "已做過錯字修正與補標點" in done


def test_report_label_reflects_actual_proofread_status():
    from ytreel.render import source_label

    assert "未經模型校對" in source_label(Transcript(segments=[], source="auto_cc", proofread=False))
    assert "已修錯字" in source_label(Transcript(segments=[], source="auto_cc", proofread=True))
    assert source_label(Transcript(segments=[], source="manual_cc")) == "人工 CC 字幕"


def test_proofread_flag_false_when_every_chunk_rejected():
    from ytreel.correct import CleanReport, llm_proofread

    class _Boom:
        def complete_text(self, **kw):
            raise RuntimeError("down")

    out = llm_proofread(
        Transcript(segments=[Segment(0, 1, "原文")], source="asr"),
        _Boom(),
        report=CleanReport(),
    )
    assert out.proofread is False
