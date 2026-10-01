"""逐字稿清理與改錯字。

分兩層：
1. 規則層（離線、可重現）：去掉 YouTube 在中文詞之間塞的空格、標點正規化、
   簡轉繁、查錯字表。
2. 模型層（LLM 校對）：修同音錯字、專有名詞、補標點。這一層最容易出事的
   地方是模型「順手改寫或摘要」，所以每個 chunk 都做長度守門，超出範圍就
   退回原文並記錄警告。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from ytreel.transcript import Segment, Transcript

_BUILTIN_FIXES = Path(__file__).parent / "data" / "fixes.json"

_CJK = r"㐀-鿿豈-﫿぀-ヿ"
_CJK_SPACE = re.compile(rf"(?<=[{_CJK}])[  ]+(?=[{_CJK}])")
_CJK_SPACE_PUNCT = re.compile(r"[  ]+(?=[，。！？；：、）」』])|(?<=[（「『])[  ]+")
_MULTI_SPACE = re.compile(r"[  ]{2,}")
_REPEAT_PUNCT = re.compile(r"([，。！？、；：])\1{1,}")

# 口語語助詞 / 填充詞（--strip-fillers 時才會動用）
_FILLERS = [
    "呃", "嗯", "欸", "唉", "啊這個", "那個那個",
    "然後然後", "就是就是", "對對對", "嗯嗯",
]

_PUNCT = re.compile(r"[，。！？、；：,.!?;:…]")


def punctuation_density(text: str) -> float:
    """每個字平均有多少標點。中文書面文字約 0.06-0.12。"""
    if not text:
        return 1.0
    return len(_PUNCT.findall(text)) / len(text)


def needs_proofread(segments, threshold: float = 0.02) -> bool:
    """判斷逐字稿是否需要補標點／改錯字。

    YouTube 自動字幕與多數 ASR 輸出完全沒有標點，密度會接近 0。有些「人工」
    字幕其實也是機器產的，所以用內容判斷比用來源標籤可靠。
    """
    text = "".join(s.text for s in segments)
    return punctuation_density(text) < threshold


# 只收「繁體中文不會用到」的簡化字。像「系」這種繁簡共用字不能放進來，
# 否則「系統」就會被誤判成簡體。
# 簡體專用字（以 OpenCC 驗證：轉繁之後會變成別的字）。
# 繁簡共用字 —— 例如 系、西、言 —— 絕對不能列進來，否則繁體文本會被誤判。
# tests/test_correct.py 有一條測試會用 OpenCC 守住這件事。
_SIMPLIFIED_PROBE = re.compile(r"[业东个为么习书产们会体关写发变员图声实应开录总择时机构样没济现电组织经结视认识话该语说读软过还这选际频题验]")
_SIMPLIFIED_MIN_HITS = 2


@dataclass
class CleanReport:
    rule_hits: dict[str, int] = field(default_factory=dict)
    llm_chunks: int = 0
    llm_rejected: int = 0
    warnings: list[str] = field(default_factory=list)
    converted_to_traditional: bool = False

    def summary(self) -> str:
        bits = []
        total = sum(self.rule_hits.values())
        if total:
            top = sorted(self.rule_hits.items(), key=lambda kv: -kv[1])[:6]
            bits.append(
                "規則修正 " + str(total) + " 處（" + "、".join(f"{k}×{v}" for k, v in top) + "）"
            )
        if self.converted_to_traditional:
            bits.append("已簡轉繁")
        if self.llm_chunks:
            bits.append(f"LLM 校對 {self.llm_chunks} 段")
        if self.llm_rejected:
            bits.append(f"{self.llm_rejected} 段因長度異常退回原文")
        return "；".join(bits) if bits else "未做任何修正"


def load_fixes(extra: str | Path | None = None, include_style: bool = True) -> list[dict]:
    data = json.loads(_BUILTIN_FIXES.read_text(encoding="utf-8"))
    rules: list[dict] = list(data.get("rules", []))
    if include_style:
        rules += data.get("style", [])
    if extra:
        user = json.loads(Path(extra).read_text(encoding="utf-8"))
        if isinstance(user, dict):
            user_rules = list(user.get("rules", [])) + list(user.get("style", []))
        else:
            user_rules = list(user)
        by_find = {r["find"]: r for r in rules}
        for r in user_rules:
            by_find[r["find"]] = r
        rules = list(by_find.values())
    # 長的規則先套，避免短規則先改掉長規則要比對的字串
    rules.sort(key=lambda r: -len(r["find"]))
    return rules


def _compile(rule: dict) -> re.Pattern[str]:
    pat = re.escape(rule["find"])
    if nf := rule.get("not_followed_by"):
        pat += f"(?![{re.escape(nf)}])"
    if nb := rule.get("not_preceded_by"):
        pat = f"(?<![{re.escape(nb)}])" + pat
    return re.compile(pat)


def strip_word_spaces(text: str) -> str:
    """去掉 YouTube 自動字幕在中文字之間插入的空格，但保留中英之間的空格。"""
    prev = None
    while prev != text:
        prev = text
        text = _CJK_SPACE.sub("", text)
    text = _CJK_SPACE_PUNCT.sub("", text)
    return _MULTI_SPACE.sub(" ", text).strip()


def normalize_punctuation(text: str) -> str:
    text = text.replace("...", "…").replace("，，", "，")
    text = _REPEAT_PUNCT.sub(r"\1", text)
    # 半形標點在中文句子裡轉全形
    if re.search(rf"[{_CJK}]", text):
        for half, full in ((",", "，"), (";", "；"), ("?", "？"), ("!", "！")):
            text = re.sub(rf"(?<=[{_CJK}]){re.escape(half)}", full, text)
        text = re.sub(rf"(?<=[{_CJK}])\.(?![0-9a-zA-Z])", "。", text)
    return text.strip()


def looks_simplified(text: str, sample: int = 4000) -> bool:
    """用簡化字出現次數判斷是否為簡體文本。

    門檻設在 2 次，單一個字就判定太容易誤判（例如引用了一個簡體詞）。
    """
    return len(_SIMPLIFIED_PROBE.findall(text[:sample])) >= _SIMPLIFIED_MIN_HITS


def to_traditional(text: str) -> tuple[str, bool]:
    """用 OpenCC 做簡轉繁（含台灣用詞轉換）。沒安裝 opencc 就原樣回傳。"""
    try:
        from opencc import OpenCC  # type: ignore
    except ImportError:
        return text, False
    for cfg in ("s2twp", "s2tw", "s2t"):
        try:
            return OpenCC(cfg).convert(text), True
        except Exception:  # noqa: BLE001 - 設定檔缺失就換下一個
            continue
    return text, False


def strip_fillers(text: str) -> str:
    for f in sorted(_FILLERS, key=len, reverse=True):
        text = text.replace(f, "")
    return _MULTI_SPACE.sub(" ", text).strip()


def apply_rules(
    transcript: Transcript,
    fixes: Sequence[dict],
    *,
    traditional: bool = True,
    fillers: bool = False,
    report: CleanReport | None = None,
) -> Transcript:
    """離線規則層。回傳新的 Transcript，不改原件。"""
    report = report or CleanReport()
    compiled = [(r, _compile(r)) for r in fixes]

    joined = "\n".join(s.text for s in transcript.segments)
    convert = traditional and looks_simplified(joined)

    out: list[Segment] = []
    for seg in transcript.segments:
        text = strip_word_spaces(seg.text)
        if convert:
            text, ok = to_traditional(text)
            report.converted_to_traditional = report.converted_to_traditional or ok
        if fillers:
            text = strip_fillers(text)
        for rule, pat in compiled:
            text, n = pat.subn(rule["replace"], text)
            if n:
                key = f"{rule['find']}→{rule['replace']}"
                report.rule_hits[key] = report.rule_hits.get(key, 0) + n
        text = normalize_punctuation(text)
        if text:
            out.append(Segment(seg.start, seg.end, text))

    if convert and not report.converted_to_traditional:
        report.warnings.append(
            "偵測到簡體字但沒有安裝 OpenCC，未做簡轉繁。可執行：pip install 'ytreel[zh]'"
        )

    return Transcript(
        segments=out,
        source=transcript.source,
        lang=transcript.lang,
        notes=list(transcript.notes),
        proofread=transcript.proofread,
    )


# --------------------------------------------------------------------------
# LLM 校對層
# --------------------------------------------------------------------------

def chunk_segments(
    segments: Sequence[Segment], max_chars: int = 1600
) -> list[list[Segment]]:
    chunks: list[list[Segment]] = []
    cur: list[Segment] = []
    size = 0
    for seg in segments:
        if cur and size + len(seg.text) > max_chars:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(seg)
        size += len(seg.text)
    if cur:
        chunks.append(cur)
    return chunks


def _numbered(segs: Sequence[Segment]) -> str:
    return "\n".join(f"{i + 1}\t{s.text}" for i, s in enumerate(segs))


def _parse_numbered(raw: str, expected: int) -> list[str] | None:
    """解析「編號<TAB>內容」的回覆。行數不符就視為失敗。"""
    lines: dict[int, str] = {}
    for line in raw.replace("\r", "").split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d{1,4})[\t.、:：)\]]?\s*(.*)$", line)
        if not m:
            continue
        idx = int(m.group(1))
        if 1 <= idx <= expected:
            lines[idx] = m.group(2).strip()
    if len(lines) != expected:
        return None
    return [lines[i] for i in range(1, expected + 1)]


def llm_proofread(
    transcript: Transcript,
    llm,
    *,
    glossary: Sequence[str] = (),
    chunk_chars: int = 1600,
    min_ratio: float = 0.55,
    max_ratio: float = 1.8,
    ratio_min_chars: int = 50,
    report: CleanReport | None = None,
    progress=None,
) -> Transcript:
    """逐段請模型改錯字、補標點，並守門避免模型順手改寫或摘要。"""
    from ytreel.prompts import PROOFREAD_SYSTEM, proofread_prompt

    report = report or CleanReport()
    chunks = chunk_segments(transcript.segments, chunk_chars)
    fixed: list[Segment] = []

    for i, chunk in enumerate(chunks):
        if progress:
            progress(i + 1, len(chunks))
        body = _numbered(chunk)
        prev_tail = fixed[-1].text if fixed else ""
        try:
            raw = llm.complete_text(
                system=PROOFREAD_SYSTEM,
                prompt=proofread_prompt(body, glossary=glossary, prev_tail=prev_tail),
                # 留足餘裕：thinking token 也計入 max_tokens，
                # 且中文字元常常不只一個 token。只有實際產生的才計費。
                max_tokens=max(4096, len(body) * 4),
                effort="low",
            )
        except Exception as exc:  # noqa: BLE001 - 單段失敗不該讓整支中斷
            report.llm_rejected += 1
            report.warnings.append(f"第 {i + 1} 段校對失敗，保留原文：{exc}")
            fixed.extend(Segment(s.start, s.end, s.text) for s in chunk)
            continue

        texts = _parse_numbered(raw, len(chunk))
        if texts is None:
            report.llm_rejected += 1
            report.warnings.append(f"第 {i + 1} 段回覆行數不符，保留原文")
            fixed.extend(Segment(s.start, s.end, s.text) for s in chunk)
            continue

        orig_len = sum(len(s.text) for s in chunk)
        new_len = sum(len(t) for t in texts)
        ratio = new_len / orig_len if orig_len else 1.0
        # 長度守門只對夠長的段落有意義：很短的段落光是補上標點，比值就會
        # 大幅跳動（「甲」→「甲。」就是 2.0），而短段落本來也藏不住摘要。
        if orig_len >= ratio_min_chars and not (min_ratio <= ratio <= max_ratio):
            report.llm_rejected += 1
            report.warnings.append(
                f"第 {i + 1} 段長度比 {ratio:.2f} 超出容許範圍（疑似被改寫或摘要），保留原文"
            )
            fixed.extend(Segment(s.start, s.end, s.text) for s in chunk)
            continue

        report.llm_chunks += 1
        for seg, text in zip(chunk, texts):
            fixed.append(Segment(seg.start, seg.end, text or seg.text))

    return Transcript(
        segments=fixed,
        source=transcript.source,
        lang=transcript.lang,
        notes=list(transcript.notes),
        # 全部段落都退回原文時，等於沒校對成功
        proofread=report.llm_chunks > 0,
    )
