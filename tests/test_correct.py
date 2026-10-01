import pytest

from ytreel.correct import (
    CleanReport,
    _SIMPLIFIED_PROBE,
    apply_rules,
    chunk_segments,
    llm_proofread,
    load_fixes,
    looks_simplified,
    needs_proofread,
    normalize_punctuation,
    punctuation_density,
    strip_fillers,
    strip_word_spaces,
)
from ytreel.transcript import Segment, Transcript


def _t(*texts):
    return Transcript(segments=[Segment(i, i + 1, t) for i, t in enumerate(texts)])


# --- 空格 / 標點 ---------------------------------------------------------

def test_removes_youtube_word_spaces_between_cjk():
    assert strip_word_spaces("大家好 歡迎 來到 這個 頻道") == "大家好歡迎來到這個頻道"


def test_keeps_spaces_around_latin():
    assert strip_word_spaces("我用 Python 寫 的 code 很 好") == "我用 Python 寫的 code 很好"


def test_removes_space_before_cjk_punctuation():
    assert strip_word_spaces("真的 嗎 ？") == "真的嗎？"


def test_halfwidth_punctuation_becomes_fullwidth_in_chinese():
    assert normalize_punctuation("真的嗎?我覺得,這樣不好.") == "真的嗎？我覺得，這樣不好。"


def test_decimal_point_survives():
    assert "3.14" in normalize_punctuation("圓周率是3.14沒錯")


def test_repeated_punctuation_collapses():
    assert normalize_punctuation("真的嗎？？？") == "真的嗎？"


# --- 錯字表 --------------------------------------------------------------

def test_builtin_fixes_applied():
    report = CleanReport()
    out = apply_rules(_t("我因該以經講過了", "在次強調一但開始"), load_fixes(), report=report)
    assert [s.text for s in out.segments] == ["我應該已經講過了", "再次強調一旦開始"]
    assert sum(report.rule_hits.values()) == 4


def test_negative_lookahead_protects_valid_word():
    """「在來米」是正確的詞，不可以被「在來→再來」規則改掉。"""
    out = apply_rules(_t("這是在來米做的"), load_fixes())
    assert out.segments[0].text == "這是在來米做的"
    out2 = apply_rules(_t("在來看下一個"), load_fixes())
    assert out2.segments[0].text == "再來看下一個"


def test_no_builtin_rule_is_a_noop():
    for rule in load_fixes():
        assert rule["find"] != rule["replace"]


def test_longer_rules_apply_first():
    finds = [r["find"] for r in load_fixes()]
    assert finds == sorted(finds, key=len, reverse=True)


def test_user_fixes_override_builtin(tmp_path):
    f = tmp_path / "my.json"
    f.write_text('{"rules":[{"find":"柏格","replace":"伯格"}]}', encoding="utf-8")
    out = apply_rules(_t("約翰柏格說"), load_fixes(f))
    assert out.segments[0].text == "約翰伯格說"


def test_style_fixes_can_be_disabled():
    assert apply_rules(_t("大部份人"), load_fixes(include_style=True)).segments[0].text == "大部分人"
    assert apply_rules(_t("大部份人"), load_fixes(include_style=False)).segments[0].text == "大部份人"


# --- 簡體偵測 ------------------------------------------------------------

def test_traditional_text_is_not_flagged_as_simplified():
    """系、西、言 等繁簡共用字曾造成誤判，這條測試守住它。"""
    assert not looks_simplified("這個東西的語言系統沒關係，我把目標換成系統")


def test_simplified_text_is_flagged():
    assert looks_simplified("这个视频说过时间很重要，我们应该开发实现")


def test_single_simplified_char_is_not_enough():
    assert not looks_simplified("這篇文章引用了「软件」這個詞")


def test_probe_contains_no_shared_characters():
    """清單裡不能有繁體中文也會用到的字，用 OpenCC 驗。"""
    opencc = pytest.importorskip("opencc")
    s2t = opencc.OpenCC("s2t")
    chars = _SIMPLIFIED_PROBE.pattern.split("[")[1].split("]")[0]
    shared = [c for c in chars if s2t.convert(c) == c]
    assert shared == [], f"繁簡共用字不該列入：{''.join(shared)}"


def test_simplified_is_converted_when_opencc_available():
    pytest.importorskip("opencc")
    report = CleanReport()
    out = apply_rules(_t("这个视频说的软件很好", "我们应该开发实现"), load_fixes(), report=report)
    assert report.converted_to_traditional
    joined = "".join(s.text for s in out.segments)
    assert "影片" in joined and "軟體" in joined
    assert not looks_simplified(joined)


# --- 需不需要校對 --------------------------------------------------------

def test_unpunctuated_transcript_needs_proofread():
    assert needs_proofread([Segment(0, 1, "大家好歡迎來到這個頻道今天我們要聊的是複利這件事")])


def test_punctuated_transcript_does_not():
    assert not needs_proofread([Segment(0, 1, "大家好，歡迎來到頻道。今天要聊複利。")])


def test_punctuation_density_of_empty_text():
    assert punctuation_density("") == 1.0


# --- 語助詞 --------------------------------------------------------------

def test_strip_fillers_is_opt_in():
    assert strip_fillers("呃這個嗯很重要") == "這個很重要"
    assert apply_rules(_t("呃這個嗯很重要"), []).segments[0].text == "呃這個嗯很重要"


# --- 分段 ----------------------------------------------------------------

def test_chunk_segments_respects_budget():
    segs = [Segment(i, i + 1, "字" * 40) for i in range(10)]
    chunks = chunk_segments(segs, max_chars=100)
    assert sum(len(c) for c in chunks) == 10
    assert all(sum(len(s.text) for s in c) <= 140 for c in chunks)


def test_oversized_single_segment_still_emitted():
    segs = [Segment(0, 1, "字" * 500)]
    assert len(chunk_segments(segs, max_chars=100)) == 1


# --- LLM 校對的守門機制 --------------------------------------------------

class _FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def complete_text(self, *, system, prompt, max_tokens, effort="medium"):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_proofread_applies_model_output():
    llm = _FakeLLM("1\t我應該已經講過了。\n2\t再次強調，一旦開始。")
    report = CleanReport()
    out = llm_proofread(_t("我因該以經講過了", "在次強調一但開始"), llm, report=report)
    assert [s.text for s in out.segments] == ["我應該已經講過了。", "再次強調，一旦開始。"]
    assert report.llm_chunks == 1 and report.llm_rejected == 0


def test_proofread_keeps_timestamps():
    llm = _FakeLLM("1\t甲。\n2\t乙。")
    src = Transcript(segments=[Segment(5, 9, "甲"), Segment(9, 14, "乙")])
    out = llm_proofread(src, llm)
    assert [(s.start, s.end) for s in out.segments] == [(5, 9), (9, 14)]


def test_proofread_rejects_summarised_output():
    """模型偷偷摘要時，長度守門要退回原文。"""
    llm = _FakeLLM("1\t短\n2\t短")
    report = CleanReport()
    original = [
        "影片裡講了很長的一段話必須完整保留下來，這段話裡面有數字也有例子，一個字都不能少",
        "第二段也一樣很長，不可以被壓縮掉，因為摘要會把講者的論證過程整個抹掉",
    ]
    out = llm_proofread(_t(*original), llm, report=report)
    assert [s.text for s in out.segments] == original
    assert report.llm_rejected == 1
    assert any("長度比" in w for w in report.warnings)


def test_proofread_rejects_wrong_line_count():
    llm = _FakeLLM("1\t只回了一行")
    report = CleanReport()
    out = llm_proofread(_t("第一行", "第二行"), llm, report=report)
    assert [s.text for s in out.segments] == ["第一行", "第二行"]
    assert any("行數不符" in w for w in report.warnings)


def test_proofread_survives_model_error():
    report = CleanReport()
    out = llm_proofread(_t("原文一", "原文二"), _FakeLLM(RuntimeError("boom")), report=report)
    assert [s.text for s in out.segments] == ["原文一", "原文二"]
    assert report.llm_rejected == 1


def test_proofread_tolerates_numbering_variants():
    llm = _FakeLLM("1. 甲。\n2. 乙。")
    out = llm_proofread(_t("甲", "乙"), llm)
    assert [s.text for s in out.segments] == ["甲。", "乙。"]


def test_short_chunk_is_not_rejected_for_adding_punctuation():
    """短段落補標點會讓長度比飆高，不該因此被退回。"""
    llm = _FakeLLM("1\t甲。\n2\t乙。")
    report = CleanReport()
    out = llm_proofread(_t("甲", "乙"), llm, report=report)
    assert [s.text for s in out.segments] == ["甲。", "乙。"]
    assert report.llm_rejected == 0


def test_long_chunk_still_guarded_against_summarising():
    long_a, long_b = "甲" * 60, "乙" * 60
    llm = _FakeLLM("1\t甲\n2\t乙")
    report = CleanReport()
    out = llm_proofread(_t(long_a, long_b), llm, report=report)
    assert [s.text for s in out.segments] == [long_a, long_b]
    assert report.llm_rejected == 1
