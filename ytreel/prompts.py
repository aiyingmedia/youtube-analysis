"""提示詞。全部用繁體中文/台灣用語，輸出也要求台灣口語。"""

from __future__ import annotations

from typing import Sequence

# --------------------------------------------------------------------------
# 校對（改錯字）
# --------------------------------------------------------------------------

PROOFREAD_SYSTEM = """你是中文逐字稿校對員，專門處理語音辨識（ASR）與 YouTube 自動字幕的輸出。

你只做這五件事：
1. 修正同音錯字與音近錯字（例如「因該」→「應該」、「在次」→「再次」）。
2. 修正被辨識錯的專有名詞：人名、品牌、地名、技術名詞、書名。
3. 補上標點符號（，。！？、：）並斷句。自動字幕通常完全沒有標點。
4. 把簡體字改成繁體字，用台灣用語（例如「视频」→「影片」、「软件」→「軟體」）。
5. 修正明顯的辨識破碎，例如重複的字詞（「就是就是」→「就是」）。

你絕對不做這些事：
- 不改寫句子、不潤稿、不調整語氣、不把口語改成書面語。
- 不摘要、不刪減內容、不合併句子、不補充原文沒有的資訊。
- 不翻譯。
- 不改動行數，也不改動每行的編號。

輸出格式：每行一筆，格式為「編號<TAB>修正後的文字」，行數必須與輸入完全相同。
不要加任何說明、前言或 Markdown 標記。如果某一行本來就沒問題，照原樣輸出。"""


def proofread_prompt(
    numbered_body: str, glossary: Sequence[str] = (), prev_tail: str = ""
) -> str:
    parts = []
    if glossary:
        parts.append(
            "這支影片會出現的專有名詞（請以這些寫法為準）：\n"
            + "、".join(glossary)
            + "\n"
        )
    if prev_tail:
        parts.append(f"上一段的最後一句（只是上下文，不要輸出）：{prev_tail}\n")
    parts.append(
        "請校對以下逐字稿。共 "
        + str(len(numbered_body.strip().split(chr(10))))
        + " 行，輸出也必須是同樣行數：\n\n"
        + numbered_body
    )
    return "\n".join(parts)


# --------------------------------------------------------------------------
# 分析 + Reel 腳本
# --------------------------------------------------------------------------

ANALYZE_SYSTEM = """你同時是兩個角色：

一、內容編輯。你擅長把一支長影片讀成「作者到底主張什麼、憑什麼這樣主張」，
而不是把影片講過的名詞列一遍。你寫的摘要讀完就知道影片的立場。

二、短影音編劇。你寫過大量表現好的 IG Reel 口播稿，知道一支 Reel 只能有一個
論點，知道前三秒決定一切，也知道觀眾滑掉的原因通常是「這跟我有什麼關係」
沒有在開頭就講清楚。

寫作規則：
- 一律使用繁體中文與台灣用語。
- 口播稿要能直接念出口：短句、口語、主動語態。禁止書面語連接詞（此外、綜上所述、
  值得一提的是、本影片）。禁止在口播稿裡放 emoji 或括號說明。
- 中文口播速度以每秒約 4 個字估算。30 秒約 120 字，45 秒約 180 字，60 秒約 240 字。
  est_seconds 要跟 full_script 的實際字數對得上。

論點（thesis）的標準，這是最重要的一條：
- 必須是一句可以被反駁的主張，有立場、有方向。
- 「複利很重要」「心態會影響結果」這種不算論點，因為沒人會反對。
- 「你每天多做的那一點，十年後不會變成十倍，而是變成別人追不上的門檻」才算論點。
- 每一支 Reel 的論點必須彼此不同，不可以是同一句話換個說法。
- 論點必須出自影片內容。影片沒說的、你自己推論出來的，不要寫進去。

精華（取材）的標準：
- 優先選影片裡最具體的東西：數字、對比、案例、講者的親身經歷、反直覺的結論。
- 避免選影片裡最籠統的那幾句（通常是開場與結尾的客套話）。
- source_timestamps 只能填逐字稿裡真實出現過的時間碼，不可以自己估一個。

誠實規則：影片沒有提到的事實、數字、人名，一律不可以寫進任何欄位。
如果逐字稿品質太差（例如大量辨識錯誤導致語意不明），就在 summary 裡直說。"""


def analyze_prompt(
    *,
    title: str,
    uploader: str,
    duration_text: str,
    url: str,
    transcript_body: str,
    transcript_source: str,
    transcript_proofread: bool = False,
    reels: int = 4,
    seconds: int,
    tone: str = "",
    audience: str = "",
    glossary: Sequence[str] = (),
    chapters: Sequence[dict] = (),
    section_notes: str = "",
) -> str:
    machine = {
        "auto_cc": "YouTube 自動字幕",
        "asr": "語音辨識（Whisper）產生",
    }.get(transcript_source)
    if machine:
        source_note = machine + (
            "，已做過錯字修正與補標點，仍可能有殘留辨識錯誤"
            if transcript_proofread
            else "，只做過規則修正、沒有經過模型校對，所以可能缺標點且有同音錯字。"
            "遇到讀不通的句子請依上下文推斷，不要當成講者真的這樣說"
        )
    elif transcript_source == "manual_cc":
        source_note = "人工上傳的 CC 字幕，品質高"
    else:
        source_note = transcript_source

    head = [
        "# 影片資訊",
        f"標題：{title}",
        f"頻道：{uploader or '未知'}",
        f"長度：{duration_text}",
        f"連結：{url}",
        f"逐字稿來源：{source_note}",
    ]
    if chapters:
        head.append("影片章節：")
        for ch in chapters[:30]:
            from ytreel.transcript import format_stamp

            head.append(f"  [{format_stamp(ch.get('start_time', 0))}] {ch.get('title', '')}")
    if glossary:
        head.append("專有名詞：" + "、".join(glossary))

    task = [
        "",
        "# 你的任務",
        "讀完下面的逐字稿，產出三樣東西：",
        "",
        "1. **摘要**：one_liner（一句話）、audience（誰該看）、summary（250-450 字段落）。",
        "   summary 要寫出影片的立場和主要論證路徑，不是把講過的話重排一次。",
        "",
        "2. **重點整理**：key_points 5-8 個，依影片順序，每個都要附真實時間碼。"
        "另外挑 2-4 句金句（quotes）。",
        "",
        f"3. **IG Reel 口播稿 {reels} 支**：每支一個獨立論點，目標長度約 {seconds} 秒。",
        "   每支都要能單獨成立 —— 觀眾只看到這一支，也要看得懂、被說服。",
        "   beats 請排成 3-5 段，依序是：鉤子 → 展開/證據 → 反轉或深化 → 收束 + CTA。",
        "",
        "選題時，這幾支 Reel 要涵蓋影片最有價值的不同面向，",
        "不要全部集中在影片的同一個段落，也不要只挑最好講的那一個。",
    ]
    if tone:
        task.append(f"\n語氣要求：{tone}")
    if audience:
        task.append(f"目標觀眾：{audience}")

    body = ["", "# 逐字稿", ""]
    if section_notes:
        body = [
            "",
            "# 分段筆記（由完整逐字稿先行整理，時間碼可信）",
            "",
            section_notes,
            "",
            "# 逐字稿（節錄：開頭與結尾）",
            "",
        ]
    body.append(transcript_body)

    return "\n".join(head + task + body)


MAP_SYSTEM = """你在替一支長影片做分段筆記，供後續寫摘要與短影音腳本使用。

要求：
- 忠實記錄講者說了什麼、理由是什麼，不要加入你自己的評論。
- 每一條要點都要附上時間碼，時間碼只能用逐字稿裡出現過的。
- 優先保留具體的東西：數字、案例、對比、反直覺的結論、講者的親身經歷。
- 一律使用繁體中文與台灣用語。"""


def map_prompt(chunk_body: str, index: int, total: int) -> str:
    return (
        f"這是整支影片的第 {index}/{total} 個區塊。請切成幾個小節並做筆記。\n\n"
        f"{chunk_body}"
    )
