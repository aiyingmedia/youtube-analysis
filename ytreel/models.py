"""模型輸出的結構（結構化輸出 schema + 本地驗證）。

刻意不給預設值，讓所有欄位都是 required —— 結構化輸出的 strict schema 要求
如此，也避免模型偷懶跳過欄位。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class KeyPoint(BaseModel):
    title: str = Field(description="重點標題，8-20 字，要有資訊量，不要用「觀點一」這種空標題")
    detail: str = Field(description="說明這個重點，2-4 句，寫清楚講者的理由或證據")
    timestamp: str = Field(description="這個重點在影片中的時間碼，格式 m:ss 或 h:mm:ss，必須來自逐字稿")


class Quote(BaseModel):
    text: str = Field(description="影片中的金句，盡量貼近原話，不要超過 50 字")
    timestamp: str = Field(description="金句出現的時間碼")
    why: str = Field(description="一句話說明這句為什麼有力量")


class Beat(BaseModel):
    label: str = Field(description="這一段的角色與秒數，例如「0-3 秒｜鉤子」")
    voiceover: str = Field(description="這一段要念出來的口播逐字稿")
    visual: str = Field(description="畫面建議：拍什麼、放什麼素材、什麼運鏡")
    on_screen: str = Field(description="螢幕上的字卡文字，10 字以內；不需要字卡就寫「—」")


class ReelScript(BaseModel):
    title: str = Field(
        description="這支 Reel 的短標題，4-12 字，用來辨識主題。"
        "不要自己加「Reel 1」之類的編號前綴"
    )
    thesis: str = Field(
        description="這支影片的論點：一句可以被反駁的主張。"
        "不可以是「XX 很重要」這種沒有立場的描述"
    )
    why_now: str = Field(description="一句話說明觀眾為什麼現在要在意這件事")
    hook: str = Field(description="前 3 秒要念的話，具體、有張力，不要自我介紹或問候")
    beats: list[Beat] = Field(description="分鏡，3-5 段，依序涵蓋鉤子、論證、收束")
    full_script: str = Field(
        description="完整口播逐字稿，就是把 beats 的 voiceover 串起來的乾淨版本，"
        "可以直接拿去念；台灣口語、短句、不要書面語、不要 emoji"
    )
    cta: str = Field(description="結尾行動呼籲，一句話")
    est_seconds: int = Field(description="預估口播秒數（以中文每秒約 4 字估算）")
    source_timestamps: list[str] = Field(
        description="這支腳本取材的影片時間碼清單，必須是逐字稿裡真實存在的時間"
    )
    caption: str = Field(description="IG 貼文文案，2-4 句，可以用 emoji")
    hashtags: list[str] = Field(description="5-8 個 hashtag，含 # 符號，繁中與英文混用皆可")


class Analysis(BaseModel):
    one_liner: str = Field(description="用一句話說完整支影片在講什麼，40 字以內")
    audience: str = Field(description="這支影片最適合誰看，一句話")
    summary: str = Field(description="摘要，250-450 字，寫成通順的段落，不要條列")
    key_points: list[KeyPoint] = Field(description="重點整理，5-8 個，依影片順序")
    quotes: list[Quote] = Field(description="金句，2-4 句")
    reels: list[ReelScript] = Field(description="IG Reel 口播稿")


class SectionNotes(BaseModel):
    """長影片 map-reduce 的中間產物。"""

    section_title: str = Field(description="這一段的小標，10-20 字")
    timestamp_range: str = Field(description="這一段的時間範圍，例如 12:30-19:05")
    points: list[str] = Field(description="這一段的要點，3-6 條，每條一句話並附上時間碼")
    quotes: list[str] = Field(description="這一段值得引用的原話，0-3 句，附時間碼")


class SectionNotesList(BaseModel):
    sections: list[SectionNotes] = Field(description="這個區塊切出來的小節筆記")
