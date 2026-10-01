from pathlib import Path

from ytreel.transcript import (
    Segment,
    Transcript,
    format_stamp,
    merge_segments,
    parse_json3,
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
