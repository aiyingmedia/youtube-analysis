from ytreel.youtube import (
    VideoInfo,
    _explain,
    has_js_runtime,
    js_runtime_args,
    pick_subtitle_lang,
    ytdlp_command,
)


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


# --- JS 執行環境 ---------------------------------------------------------

def _which_only(*present):
    return lambda name: f"/usr/bin/{name}" if name in present else None


def test_deno_needs_no_flag(monkeypatch):
    """deno 是 yt-dlp 的預設，有它就不用另外指定。"""
    monkeypatch.setattr("ytreel.youtube.shutil.which", _which_only("deno", "node"))
    assert js_runtime_args() == []


def test_node_is_enabled_explicitly(monkeypatch):
    monkeypatch.setattr("ytreel.youtube.shutil.which", _which_only("node"))
    assert js_runtime_args() == ["--js-runtimes", "node"]


def test_bun_used_when_node_missing(monkeypatch):
    monkeypatch.setattr("ytreel.youtube.shutil.which", _which_only("bun"))
    assert js_runtime_args() == ["--js-runtimes", "bun"]


def test_no_runtime_detected(monkeypatch):
    monkeypatch.setattr("ytreel.youtube.shutil.which", _which_only())
    assert js_runtime_args() == []
    assert has_js_runtime() is False


# --- 錯誤說明 ------------------------------------------------------------

def test_explain_bot_check_mentions_unreliable_subtitles():
    msg = _explain("ERROR: [youtube] abc: Sign in to confirm you’re not a bot. Use --cookies")
    assert "機器人" in msg
    assert "不一定是真的沒有" in msg  # 被擋時字幕清單也是空的
    assert "顯示轉錄稿" in msg


def test_explain_bot_check_wins_over_proxy_warning():
    """被導到 Google 驗證頁時，警告裡會有 tunnel 403；不能因此誤判成網路政策問題。"""
    stderr = (
        "WARNING: [youtube] ('Unable to connect to proxy', OSError('Tunnel connection failed: "
        "403 Forbidden')). Retrying (1/3)...\n"
        "ERROR: [youtube] abc: Sign in to confirm you’re not a bot."
    )
    msg = _explain(stderr)
    assert "機器人" in msg
    assert "網路政策" not in msg


def test_explain_stream_403():
    msg = _explain("ERROR: unable to download video data: HTTP Error 403: Forbidden")
    assert "403" in msg and "機器人" in msg


def test_explain_page_reload_is_bot_check():
    assert "機器人" in _explain("ERROR: [youtube] abc: The page needs to be reloaded.")


def test_explain_proxy_block_mentions_wildcards():
    msg = _explain("ERROR: Unable to download API page: Tunnel connection failed: 403 Forbidden")
    assert "*.youtube.com" in msg and "*.googlevideo.com" in msg


def test_explain_missing_js_runtime_from_warning():
    stderr = (
        "WARNING: [youtube] No supported JavaScript runtime could be found.\n"
        "ERROR: something else"
    )
    assert "deno" in _explain(stderr)


def test_explain_strips_report_issue_boilerplate():
    msg = _explain(
        "ERROR: boom; please report this issue on https://github.com/yt-dlp/yt-dlp/issues?q= ,"
        " filling out the appropriate issue template."
    )
    assert msg == "ERROR: boom"


def test_explain_uses_error_line_not_trailing_warning():
    msg = _explain("ERROR: Video unavailable\nWARNING: something after")
    assert msg.splitlines()[0] == "ERROR: Video unavailable"
    assert "下架" in msg


# --- 用哪一個 yt-dlp ------------------------------------------------------

def test_prefers_yt_dlp_from_same_environment(monkeypatch):
    """PATH 上的 yt-dlp 可能是舊版，讀不到這個環境的 yt-dlp-ejs。"""
    import sys

    monkeypatch.setattr("ytreel.youtube.shutil.which", lambda name: "/usr/local/bin/yt-dlp")
    assert ytdlp_command() == [sys.executable, "-m", "yt_dlp"]


def test_falls_back_to_path_binary(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_yt_dlp(name, *args, **kwargs):
        if name == "yt_dlp":
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_yt_dlp)
    monkeypatch.setattr("ytreel.youtube.shutil.which", lambda name: "/usr/local/bin/yt-dlp")
    assert ytdlp_command() == ["/usr/local/bin/yt-dlp"]
