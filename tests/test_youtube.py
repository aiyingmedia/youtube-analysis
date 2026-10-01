from ytreel.youtube import VideoInfo, _explain, pick_subtitle_lang


def _info(manual=None, auto=None, duration=0.0):
    return VideoInfo(
        video_id="x", title="t", uploader="u", duration=duration, upload_date="",
        description="", webpage_url="", chapters=[],
        manual_subs=manual or {}, auto_subs=auto or {}, raw={},
    )


def test_manual_cc_wins_over_auto_even_in_another_language():
    """人工字幕沒有辨識錯誤，品質差距比語言差距大。"""
    assert pick_subtitle_lang(_info(manual={"en": []}, auto={"zh-Hant": []})) == ("en", "manual_cc")


def test_prefers_traditional_chinese_among_manual():
    picked = pick_subtitle_lang(_info(manual={"en": [], "zh-Hant": [], "zh-Hans": []}))
    assert picked == ("zh-Hant", "manual_cc")


def test_simplified_chosen_over_english():
    assert pick_subtitle_lang(_info(manual={"en": [], "zh-Hans": []}))[0] == "zh-Hans"


def test_language_matching_is_case_insensitive():
    assert pick_subtitle_lang(_info(manual={"ZH-HANT": []})) == ("ZH-HANT", "manual_cc")


def test_falls_back_to_any_chinese_then_any_language():
    assert pick_subtitle_lang(_info(auto={"zh-Hant-x-custom": []}))[0] == "zh-Hant-x-custom"
    assert pick_subtitle_lang(_info(auto={"ko": []})) == ("ko", "auto_cc")


def test_no_subtitles_returns_none():
    assert pick_subtitle_lang(_info()) is None


def test_duration_text():
    assert _info(duration=3725).duration_text == "1:02:05"
    assert _info(duration=0).duration_text == "未知"


def test_explain_blocked_network():
    msg = _explain("ERROR: Unable to download: Tunnel connection failed: 403 Forbidden")
    assert "網路政策" in msg


def test_explain_bot_check_suggests_cookies():
    assert "cookies" in _explain("ERROR: Sign in to confirm you're not a bot").lower()


def test_explain_unavailable():
    assert "下架" in _explain("ERROR: Video unavailable")


def test_explain_passes_through_unknown_errors():
    assert _explain("some weird failure") == "some weird failure"
