"""沒有 CC 字幕時的語音辨識。

優先用 faster-whisper（CTranslate2，CPU 也跑得動），沒有就退回 openai-whisper。

中文影片最關鍵的一招是 initial_prompt：Whisper 對中文常輸出簡體，而且專有
名詞容易亂猜。餵一段繁體中文的提示詞 + 詞彙表，可以把輸出拉回繁體並大幅
降低專有名詞錯字，等於在辨識階段就先改了一半的錯字。
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ytreel.transcript import Segment, Transcript

DEFAULT_MODEL = "large-v3"


class ASRUnavailable(RuntimeError):
    pass


def build_initial_prompt(glossary: Sequence[str] = (), lang: str | None = "zh") -> str:
    if lang and not lang.startswith("zh"):
        if glossary:
            return "Key terms: " + ", ".join(glossary) + "."
        return ""
    base = "以下是一段繁體中文的逐字稿，請使用台灣用語與繁體字，並加上標點符號。"
    if glossary:
        base += "影片中會出現這些專有名詞：" + "、".join(glossary) + "。"
    return base


def _pick_device(requested: str) -> tuple[str, str]:
    if requested != "auto":
        return requested, ("float16" if requested == "cuda" else "int8")
    try:
        import ctranslate2  # type: ignore

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:  # noqa: BLE001 - 偵測失敗就用 CPU
        pass
    return "cpu", "int8"


def transcribe(
    audio: Path,
    *,
    model_size: str = DEFAULT_MODEL,
    lang: str | None = "zh",
    glossary: Sequence[str] = (),
    device: str = "auto",
    beam_size: int = 5,
    vad: bool = True,
    progress=None,
) -> Transcript:
    """把音訊轉成逐字稿。"""
    prompt = build_initial_prompt(glossary, lang)
    try:
        return _faster_whisper(
            audio,
            model_size=model_size,
            lang=lang,
            prompt=prompt,
            device=device,
            beam_size=beam_size,
            vad=vad,
            progress=progress,
        )
    except ImportError:
        pass
    try:
        return _openai_whisper(
            audio, model_size=model_size, lang=lang, prompt=prompt, progress=progress
        )
    except ImportError as exc:
        raise ASRUnavailable(
            "這支影片沒有可用的 CC 字幕，需要語音辨識，但找不到 Whisper。\n"
            "請安裝其中一個：\n"
            "  pip install 'ytreel[asr]'     # faster-whisper，建議\n"
            "  pip install openai-whisper\n"
            "另外需要 ffmpeg（brew install ffmpeg / apt install ffmpeg）。"
        ) from exc


def _faster_whisper(
    audio: Path,
    *,
    model_size: str,
    lang: str | None,
    prompt: str,
    device: str,
    beam_size: int,
    vad: bool,
    progress=None,
) -> Transcript:
    from faster_whisper import WhisperModel  # type: ignore

    dev, compute = _pick_device(device)
    model = WhisperModel(model_size, device=dev, compute_type=compute)
    segments_iter, info = model.transcribe(
        str(audio),
        language=lang,
        initial_prompt=prompt or None,
        beam_size=beam_size,
        vad_filter=vad,
        condition_on_previous_text=False,  # 避免錯誤一路傳染下去
        word_timestamps=False,
    )
    total = float(getattr(info, "duration", 0) or 0)
    segs: list[Segment] = []
    for s in segments_iter:
        text = (s.text or "").strip()
        if text:
            segs.append(Segment(float(s.start), float(s.end), text))
        if progress and total:
            progress(min(float(s.end), total), total)
    detected = getattr(info, "language", lang) or (lang or "")
    return Transcript(
        segments=segs,
        source="asr",
        lang=detected,
        notes=[f"faster-whisper {model_size} / {dev}-{compute}"],
    )


def _openai_whisper(
    audio: Path, *, model_size: str, lang: str | None, prompt: str, progress=None
) -> Transcript:
    import whisper  # type: ignore

    model = whisper.load_model(model_size)
    result = model.transcribe(
        str(audio),
        language=lang,
        initial_prompt=prompt or None,
        condition_on_previous_text=False,
        verbose=False,
    )
    segs = [
        Segment(float(s["start"]), float(s["end"]), str(s["text"]).strip())
        for s in result.get("segments", [])
        if str(s.get("text", "")).strip()
    ]
    if progress and segs:
        progress(segs[-1].end, segs[-1].end)
    return Transcript(
        segments=segs,
        source="asr",
        lang=result.get("language") or (lang or ""),
        notes=[f"openai-whisper {model_size}"],
    )
