from pathlib import Path

import pytest

from ytreel.transcript import (
    NoTimestampsError,
    Segment,
    Transcript,
    format_stamp,
    merge_segments,
    parse_json3,
    parse_plain_transcript,
    parse_stamp,
    parse_subtitle_file,
    parse_vtt,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_auto_caption_rolling_window_is_deduped():
    """YouTube 自動字幕會重複同一句並逐字長出來，必須去重。"""
    segs = parse_subtitle_file(FIXTURES / "auto_zh.vtt")
    texts = [s.text for s in segs]
    assert texts == ["大家好 歡迎 來到 這個 頻道", "今天 我們 要 聊 的 是", "複利 這 件 事"]
    assert len(texts) == len(set(texts))


def test_inline_timing_tags_are_stripped():
    segs = parse_subtitle_file(FIXTURES / "auto_zh.vtt")
    joined = " ".join(s.text for s in segs)
    assert "<" not in joined and "</c>" not in joined


def test_srt_parsing_keeps_punctuation():
    segs = parse_subtitle_file(FIXTURES / "manual_zh.srt")
    assert len(segs) == 2
    assert segs[0].text == "第一句話，這是人工字幕。"
    assert segs[0].start == 1.0 and segs[0].end == 3.5


def test_json3_parsing():
    content = (
        '{"events":[{"tStartMs":1000,"dDurationMs":2000,"segs":[{"utf8":"你好"},'
        '{"utf8":"世界"}]},{"tStartMs":4000,"dDurationMs":1000,"segs":[{"utf8":"\\n"}]}]}'
    )
    segs = parse_json3(content)
    assert [s.text for s in segs] == ["你好世界"]
    assert segs[0].start == 1.0


def test_mm_ss_timestamps_without_hours():
    segs = parse_vtt("WEBVTT\n\n01:02.500 --> 01:05.000\n只有分秒的時間碼\n")
    assert segs[0].start == 62.5


def test_stamp_roundtrip():
    assert format_stamp(0) == "0:00"
    assert format_stamp(62) == "1:02"
    assert format_stamp(3725) == "1:02:05"
    assert parse_stamp("1:02:05") == 3725
    assert parse_stamp("[2:03]") == 123
    assert parse_stamp("") == 0.0


def test_merge_respects_sentence_ends_and_length():
    segs = [
        Segment(0, 1, "第一句。"),
        Segment(1, 2, "第二句開頭"),
        Segment(2, 3, "接著講完"),
    ]
    merged = merge_segments(segs, max_chars=50)
    assert [s.text for s in merged] == ["第一句。", "第二句開頭接著講完"]


def test_merge_does_not_glue_across_long_gaps():
    segs = [Segment(0, 1, "前面"), Segment(100, 101, "很久以後")]
    assert len(merge_segments(segs, max_chars=50, max_gap=2.0)) == 2


def test_merge_keeps_space_between_latin_words():
    segs = [Segment(0, 1, "I use"), Segment(1, 2, "Python daily")]
    assert merge_segments(segs, max_chars=100)[0].text == "I use Python daily"


def test_timestamped_every_suppresses_stamps():
    t = Transcript(segments=[Segment(0, 1, "a"), Segment(1, 2, "b"), Segment(40, 41, "c")])
    assert t.timestamped(every=0).count("[") == 3
    assert t.timestamped(every=30).count("[") == 2


def test_transcript_metrics():
    t = Transcript(segments=[Segment(0, 2, "四個字"), Segment(2, 9, "兩字")])
    assert t.char_count == 5
    assert t.duration == 9


# --- 從 YouTube「顯示轉錄稿」複製的純文字 -------------------------------

def test_plain_transcript_timestamp_on_own_line():
    content = "0:00\n大家好\n0:05\n今天要聊生育率\n"
    segs = parse_plain_transcript(content)
    assert [(s.start, s.text) for s in segs] == [(0, "大家好"), (5, "今天要聊生育率")]
    assert segs[0].end == 5  # 結束時間取下一段的開始


def test_plain_transcript_timestamp_inline():
    segs = parse_plain_transcript("0:00 大家好\n0:05 今天要聊\n")
    assert [s.text for s in segs] == ["大家好", "今天要聊"]


def test_plain_transcript_handles_hours():
    assert parse_plain_transcript("1:02:03\n最後")[0].start == 3723


def test_plain_transcript_skips_lines_before_first_timestamp():
    """複製時常常連影片標題一起帶到。"""
    segs = parse_plain_transcript("影片標題\n0:00\n內容")
    assert [s.text for s in segs] == ["內容"]


def test_plain_transcript_joins_wrapped_lines():
    segs = parse_plain_transcript("0:00\n第一行\n接著第二行\n0:09\n下一段")
    assert segs[0].text == "第一行 接著第二行"


def test_plain_transcript_without_timestamps_is_rejected():
    """不能用字數估時間碼：報告的時間碼連結全靠它。"""
    with pytest.raises(NoTimestampsError):
        parse_plain_transcript("只有文字\n沒有任何時間碼")


def test_numbers_without_colon_are_text_not_timestamps():
    segs = parse_plain_transcript("0:00\n2025\n年生育率")
    assert segs[0].text == "2025 年生育率"


def test_txt_file_routes_to_plain_parser(tmp_path):
    f = tmp_path / "transcript.txt"
    f.write_text("0:00\n大家好\n0:04\n再見", encoding="utf-8")
    assert [s.text for s in parse_subtitle_file(f)] == ["大家好", "再見"]


def test_vtt_without_extension_still_detected(tmp_path):
    f = tmp_path / "subs.txt"
    f.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n字幕\n", encoding="utf-8")
    assert [s.text for s in parse_subtitle_file(f)] == ["字幕"]
