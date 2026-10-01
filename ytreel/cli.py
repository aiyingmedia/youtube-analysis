"""ytreel 命令列介面。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ytreel import __version__
from ytreel.analyze import (
    DEFAULT_MAP_BLOCK_CHARS,
    DEFAULT_MAX_DIRECT_CHARS,
    AnalyzeReport,
    analyze,
)
from ytreel.correct import (
    CleanReport,
    apply_rules,
    llm_proofread,
    load_fixes,
    needs_proofread,
)
from ytreel.llm import LLMError, NoLLMBackend, build_backend, write_prompt_pack
from ytreel.render import (
    render_report,
    render_scripts_only,
    render_transcript_file,
)
from ytreel.transcript import Transcript, merge_segments, parse_subtitle_file
from ytreel.urls import InvalidYouTubeURL, canonical_url, extract_video_id
from ytreel.youtube import (
    DEFAULT_SUB_LANGS,
    VideoInfo,
    YtDlpError,
    download_audio,
    download_subtitle,
    pick_subtitle_lang,
    probe,
)

EPILOG = """\
範例：
  ytreel https://youtu.be/XXXXXXXXXXX
  ytreel <url> --reels 5 --seconds 30 --tone "口氣輕鬆一點，像跟朋友講話"
  ytreel <url> --glossary "複利,約翰柏格,ETF"        # 大幅降低專有名詞錯字
  ytreel <url> --glossary @terms.txt --prefer-asr    # 強制用語音辨識
  ytreel <url> --llm cli                             # 用本機 Claude Code，不用 API key
  ytreel <url> --llm none                            # 只輸出逐字稿與提示詞包

沒有 CC 字幕時會自動改用 Whisper 語音辨識，再做錯字修正。
需要語音辨識請先安裝： pip install 'ytreel[asr]' 與 ffmpeg
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ytreel",
        description="輸入 YouTube 連結，自動產出摘要、重點整理與 IG Reel 口播稿。",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("url", help="YouTube 連結或 11 碼 video id")
    p.add_argument("--version", action="version", version=f"ytreel {__version__}")

    g = p.add_argument_group("逐字稿來源")
    g.add_argument("--prefer-asr", action="store_true", help="就算有 CC 字幕也改用語音辨識")
    g.add_argument(
        "--sub-langs",
        default=",".join(DEFAULT_SUB_LANGS),
        help="字幕語言偏好，逗號分隔（預設繁中優先）",
    )
    g.add_argument("--subtitle-file", type=Path, help="直接使用本機字幕檔（.vtt/.srt/.json3）")
    g.add_argument(
        "--subtitle-kind",
        choices=("detect", "manual_cc", "auto_cc"),
        default="detect",
        help="搭配 --subtitle-file：字幕是人工還是機器產的（預設看有沒有標點自動判斷）",
    )
    g.add_argument("--audio-file", type=Path, help="直接使用本機音訊檔做語音辨識")

    g = p.add_argument_group("錯字修正")
    g.add_argument(
        "--glossary",
        default="",
        help="專有名詞，逗號分隔；或 @檔名 從檔案讀（每行一個）。會餵給 Whisper 與校對",
    )
    g.add_argument("--fixes", type=Path, help="自訂錯字表 JSON，與內建表合併")
    g.add_argument("--no-llm-proofread", action="store_true", help="只做規則修正，不叫模型校對")
    g.add_argument("--strip-fillers", action="store_true", help="移除呃、嗯這類語助詞")
    g.add_argument("--no-traditional", action="store_true", help="不做簡轉繁")
    g.add_argument("--no-style-fixes", action="store_true", help="不做用字統一（部份→部分）")
    g.add_argument("--chunk-chars", type=int, default=1600, help="校對每段字數（預設 1600）")
    g.add_argument(
        "--merge-chars",
        type=int,
        default=60,
        help="破碎字幕合併後的每句字數上限；調小可讓重點的時間碼更精準（預設 60）",
    )

    g = p.add_argument_group("輸出內容")
    g.add_argument("--reels", type=int, default=4, help="要幾支 Reel 腳本，3-5 之間（預設 4）")
    g.add_argument("--seconds", type=int, default=40, help="每支 Reel 目標秒數（預設 40）")
    g.add_argument("--tone", default="", help="語氣要求，例如「專業但不說教」")
    g.add_argument("--audience", default="", help="目標觀眾，例如「25-35 歲剛開始投資的人」")

    g = p.add_argument_group("模型")
    g.add_argument(
        "--llm",
        choices=("api", "cli", "none"),
        default="api",
        help="api=Anthropic API（預設）；cli=本機 claude 指令；none=不叫模型",
    )
    g.add_argument("--model", default=None, help="模型名稱（api 預設 claude-opus-5-5）")
    g.add_argument("--no-fallback", action="store_true", help="關閉伺服器端 refusal fallback")
    g.add_argument("--max-output-tokens", type=int, default=32_000, help="分析呼叫的輸出上限")
    g.add_argument(
        "--max-direct-chars",
        type=int,
        default=DEFAULT_MAX_DIRECT_CHARS,
        help="逐字稿超過這個字數才改走分段分析",
    )
    g.add_argument("--map-block-chars", type=int, default=DEFAULT_MAP_BLOCK_CHARS)
    g.add_argument("--timeout", type=float, default=1800.0, help="單次模型呼叫逾時秒數")

    g = p.add_argument_group("語音辨識")
    g.add_argument("--asr-model", default="large-v3", help="Whisper 模型（預設 large-v3）")
    g.add_argument("--asr-device", default="auto", choices=("auto", "cpu", "cuda"))
    g.add_argument("--lang", default="zh", help="語音辨識語言，auto 為自動偵測（預設 zh）")
    g.add_argument("--keep-audio", action="store_true", help="保留下載的音訊檔")

    g = p.add_argument_group("yt-dlp")
    g.add_argument("--cookies-from-browser", default="", help="例如 chrome / firefox / safari")
    g.add_argument("--cookies", type=Path, help="cookies.txt 路徑")
    g.add_argument(
        "--ytdlp-arg",
        action="append",
        default=[],
        help="額外傳給 yt-dlp 的參數，可重複",
    )

    g = p.add_argument_group("輸出")
    g.add_argument("-o", "--out", type=Path, default=None, help="輸出目錄（預設 out/<video_id>）")
    g.add_argument("--no-transcript-file", action="store_true", help="不輸出逐字稿檔")
    g.add_argument("-q", "--quiet", action="store_true", help="只輸出錯誤")
    return p


def parse_glossary(raw: str) -> list[str]:
    if not raw:
        return []
    if raw.startswith("@"):
        text = Path(raw[1:]).read_text(encoding="utf-8")
        items = [line.strip() for line in text.splitlines()]
    else:
        items = [x.strip() for x in raw.replace("、", ",").split(",")]
    seen: dict[str, None] = {}
    for it in items:
        if it and not it.startswith("#"):
            seen.setdefault(it, None)
    return list(seen)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    def log(msg: str) -> None:
        if not args.quiet:
            print(msg, file=sys.stderr, flush=True)

    try:
        video_id = extract_video_id(args.url)
    except InvalidYouTubeURL as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 2

    if not 1 <= args.reels <= 8:
        print("錯誤：--reels 請給 1-8 之間的數字（建議 3-5）", file=sys.stderr)
        return 2

    url = args.url if "://" in args.url else canonical_url(video_id)
    outdir = args.out or Path("out") / video_id
    rawdir = outdir / "raw"
    outdir.mkdir(parents=True, exist_ok=True)

    yt_args = list(args.ytdlp_arg)
    if args.cookies_from_browser:
        yt_args += ["--cookies-from-browser", args.cookies_from_browser]
    if args.cookies:
        yt_args += ["--cookies", str(args.cookies)]

    glossary = parse_glossary(args.glossary)
    if glossary:
        log(f"● 專有名詞 {len(glossary)} 個：{'、'.join(glossary[:8])}")

    # --- 1. 影片資訊 ----------------------------------------------------
    log(f"● 讀取影片資訊：{video_id}")
    try:
        info = probe(url, yt_args)
    except (YtDlpError, json.JSONDecodeError) as exc:
        if args.subtitle_file or args.audio_file:
            log(f"⚠ 取不到影片資訊（{exc}），改用本機檔案繼續")
            info = VideoInfo(video_id, args.subtitle_file.stem if args.subtitle_file else video_id,
                             "", 0.0, "", "", url, [], {}, {}, {})
        else:
            print(f"錯誤：{exc}", file=sys.stderr)
            return 1
    log(f"  《{info.title}》／{info.uploader or '未知頻道'}／{info.duration_text}")

    # --- 2. 逐字稿 ------------------------------------------------------
    try:
        transcript = _get_transcript(args, info, url, rawdir, glossary, yt_args, log)
    except (YtDlpError, LLMError) as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:  # ASRUnavailable 等
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1

    if not transcript.segments:
        print("錯誤：解析不出任何逐字稿內容", file=sys.stderr)
        return 1
    log(f"  逐字稿 {len(transcript.segments)} 句／{transcript.char_count:,} 字")

    # --- 3. 建立模型後端 ------------------------------------------------
    try:
        llm = build_backend(
            args.llm,
            args.model,
            use_fallbacks=not args.no_fallback,
            timeout=args.timeout,
        )
    except LLMError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    model_label = f"{llm.name}:{getattr(llm, 'model', '-')}"

    # --- 4. 清理與改錯字 ------------------------------------------------
    clean = CleanReport()
    transcript = apply_rules(
        transcript,
        load_fixes(args.fixes, include_style=not args.no_style_fixes),
        traditional=not args.no_traditional,
        fillers=args.strip_fillers,
        report=clean,
    )
    transcript = Transcript(
        segments=merge_segments(transcript.segments, max_chars=args.merge_chars),
        source=transcript.source,
        lang=transcript.lang,
        notes=transcript.notes,
        proofread=transcript.proofread,
    )
    log(f"● 規則清理：{clean.summary()}")

    needs_proofread = transcript.source in ("auto_cc", "asr")
    if needs_proofread and not args.no_llm_proofread and args.llm != "none":
        log("● LLM 校對（改錯字、補標點）…")
        transcript = llm_proofread(
            transcript,
            llm,
            glossary=glossary,
            chunk_chars=args.chunk_chars,
            report=clean,
            progress=lambda i, n: log(f"  校對 {i}/{n}") if i % 5 == 1 or i == n else None,
        )
        log(f"  {clean.summary()}")
    elif needs_proofread:
        log("⚠ 跳過 LLM 校對；自動字幕/語音辨識的錯字與缺標點會留在逐字稿裡")

    for w in clean.warnings:
        log(f"⚠ {w}")

    if not args.no_transcript_file:
        (outdir / "transcript.md").write_text(
            render_transcript_file(transcript, info), encoding="utf-8"
        )

    # --- 5. 分析 + 腳本 --------------------------------------------------
    log(f"● 分析與撰稿（{model_label}）…")
    analyzed = AnalyzeReport()
    try:
        analysis = analyze(
            transcript,
            info,
            llm,
            reels=args.reels,
            seconds=args.seconds,
            tone=args.tone,
            audience=args.audience,
            glossary=glossary,
            max_direct_chars=args.max_direct_chars,
            map_block_chars=args.map_block_chars,
            max_output_tokens=args.max_output_tokens,
            report=analyzed,
            progress=lambda stage, i, n: log(f"  {stage} {i}/{n}"),
        )
    except NoLLMBackend.Skipped as skipped:
        written = write_prompt_pack(outdir, skipped)
        log("● --llm none：已輸出逐字稿與提示詞包，沒有呼叫模型")
        for p in written:
            print(p)
        if not args.no_transcript_file:
            print(outdir / "transcript.md")
        return 0
    except LLMError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1

    for w in analyzed.warnings:
        log(f"⚠ {w}")

    # --- 6. 輸出 --------------------------------------------------------
    report_path = outdir / "report.md"
    report_path.write_text(
        render_report(
            analysis,
            info,
            transcript,
            model_label=model_label,
            clean=clean,
            analyzed=analyzed,
        ),
        encoding="utf-8",
    )
    (outdir / "scripts.txt").write_text(render_scripts_only(analysis), encoding="utf-8")
    (outdir / "analysis.json").write_text(
        analysis.model_dump_json(indent=2), encoding="utf-8"
    )

    log(f"● 完成：{len(analysis.key_points)} 個重點、{len(analysis.reels)} 支 Reel 腳本")
    print(report_path)
    print(outdir / "scripts.txt")
    print(outdir / "analysis.json")
    if not args.no_transcript_file:
        print(outdir / "transcript.md")
    return 0


def _get_transcript(args, info: VideoInfo, url: str, rawdir: Path, glossary, yt_args, log) -> Transcript:
    """依序嘗試：本機檔案 → CC 字幕 → 語音辨識。"""

    if args.subtitle_file:
        log(f"● 使用本機字幕檔：{args.subtitle_file}")
        segs = parse_subtitle_file(args.subtitle_file)
        kind = args.subtitle_kind
        if kind == "detect":
            kind = "auto_cc" if needs_proofread(segs) else "manual_cc"
            log(f"  判定為{'機器字幕（會做校對）' if kind == 'auto_cc' else '人工字幕'}")
        return Transcript(segments=segs, source=kind, lang="", notes=["本機字幕檔"])

    lang = None if args.lang == "auto" else args.lang

    if args.audio_file:
        log(f"● 使用本機音訊檔做語音辨識：{args.audio_file}")
        return _run_asr(args, Path(args.audio_file), glossary, lang, log)

    if not args.prefer_asr:
        prefs = [x.strip() for x in args.sub_langs.split(",") if x.strip()]
        picked = pick_subtitle_lang(info, prefs or DEFAULT_SUB_LANGS)
        if picked:
            sub_lang, kind = picked
            label = "人工 CC" if kind == "manual_cc" else "自動字幕"
            log(f"● 找到{label}：{sub_lang}")
            path = download_subtitle(url, sub_lang, kind, rawdir, yt_args)
            segs = parse_subtitle_file(path)
            if segs:
                if kind == "manual_cc" and needs_proofread(segs):
                    log("  這份人工字幕沒有標點，當作機器字幕處理")
                    kind = "auto_cc"
                return Transcript(
                    segments=segs,
                    source=kind,
                    lang=sub_lang,
                    notes=[f"yt-dlp {path.name}"],
                )
            log("⚠ 字幕檔解析不出內容，改用語音辨識")
        else:
            log("● 這支影片沒有 CC 字幕，改用語音辨識")

    log("● 下載音訊…")
    audio = download_audio(url, rawdir, yt_args)
    try:
        return _run_asr(args, audio, glossary, lang, log)
    finally:
        if not args.keep_audio:
            audio.unlink(missing_ok=True)


def _run_asr(args, audio: Path, glossary, lang, log) -> Transcript:
    from ytreel import asr

    log(f"● 語音辨識（{args.asr_model}）…")
    last = [0.0]

    def progress(done: float, total: float) -> None:
        if total and done - last[0] >= max(30.0, total / 20):
            last[0] = done
            log(f"  {done / total * 100:.0f}%")

    return asr.transcribe(
        audio,
        model_size=args.asr_model,
        lang=lang,
        glossary=glossary,
        device=args.asr_device,
        progress=progress,
    )
