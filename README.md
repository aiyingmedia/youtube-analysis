# ytreel

[![CI](https://github.com/aiyingmedia/youtube-analysis/actions/workflows/ci.yml/badge.svg)](https://github.com/aiyingmedia/youtube-analysis/actions/workflows/ci.yml)

輸入 YouTube 連結，自動產出**摘要**、**重點整理**，以及 **3～5 支有論點、有精華的 IG Reel 口播稿**。

沒有 CC 字幕的影片，會自動改用**語音辨識（Whisper）＋改錯字**再做分析。

```bash
ytreel https://youtu.be/XXXXXXXXXXX
```

```
out/XXXXXXXXXXX/
├── report.md       ← 主要成果：摘要 + 重點整理 + Reel 腳本（含分鏡表）
├── scripts.txt     ← 只有口播稿的純文字版，可直接貼進提詞機
├── analysis.json   ← 結構化資料，方便接到別的流程
└── transcript.md   ← 修正過的逐字稿（含時間碼）
```

---

## 安裝

```bash
pip install -e .                 # 基本功能（有 CC 字幕的影片）
pip install -e '.[all]'          # 加上語音辨識與簡轉繁
brew install deno                # 必要：yt-dlp 解析 YouTube 需要 JS 執行環境
brew install ffmpeg              # 語音辨識需要（Linux: apt install ffmpeg）
```

**為什麼要裝 deno**：YouTube 的播放驗證要跑 JavaScript 才解得開。少了它，yt-dlp
只能用降級模式，結果是格式缺漏、串流 403，**連字幕都可能被漏判成「沒有字幕」**。
已經有 Node.js 或 Bun 的話也可以，工具會自動偵測並使用。

接著設定模型憑證，二選一：

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # 用 API
# 或什麼都不用設，直接用本機已登入的 Claude Code：
ytreel <url> --llm cli
```

## 它實際上做了什麼

```
YouTube 連結
   │
   ├─ 1. 抓逐字稿
   │     ├─ 有人工 CC 字幕 → 直接用（品質最好）
   │     ├─ 只有自動字幕   → 用它，但標記為需要校對
   │     └─ 都沒有         → 下載音訊 → Whisper 語音辨識
   │
   ├─ 2. 改錯字（兩層）
   │     ├─ 規則層：去掉自動字幕的詞間空格、補正規化標點、簡轉繁、查錯字表
   │     └─ 模型層：修同音錯字與專有名詞、補標點
   │
   ├─ 3. 分析：摘要 / 重點整理（附真實時間碼）/ 金句
   │
   └─ 4. 寫 3～5 支 Reel 口播稿，每支一個獨立論點
```

### 關於「改錯字」

自動字幕與語音辨識的輸出有三個共同問題，三個都處理：

| 問題 | 做法 |
| --- | --- |
| 中文詞之間被插入空格 | 規則移除（但保留中英文之間的空格） |
| 完全沒有標點 | 模型校對時補上，並斷句 |
| 同音錯字、專有名詞亂猜 | 錯字表 + 模型校對 + 詞彙表 |

**最有效的一招是 `--glossary`。** 把影片裡的人名、品牌、術語先告訴它，這些詞會同時餵給
Whisper 的 `initial_prompt` 和校對提示詞，專有名詞的錯字會少很多：

```bash
ytreel <url> --glossary "約翰柏格,ETF,再平衡"
ytreel <url> --glossary @terms.txt          # 每行一個詞
```

校對這一步最大的風險是模型「順手把逐字稿改寫或摘要掉」。所以每一段校對回來都會做
**長度守門**：字數比值超出容許範圍，就退回原文並在報告的「處理紀錄」裡註明，寧可留著錯字
也不要悄悄弄丟內容。

### 關於「有論點」的 Reel 腳本

工具對論點（thesis）有明確要求：**必須是一句可以被反駁的主張**。

- ❌ 「複利很重要」—— 沒人會反對，這不是論點
- ✅ 「判斷要不要堅持不能看有沒有成果，因為複利的成果一定會遲到，大多數人是在爆發前放棄的」

每支腳本包含：論點、為什麼現在要看、前 3 秒鉤子、分鏡表（口播／畫面／字卡）、
完整口播逐字稿、CTA、IG 貼文文案、hashtag、取材時間碼。

秒數是用中文口播約每秒 4 字估算的，報告會同時列出**預估秒數**和**實際字數換算的秒數**，
對不上的時候會在「處理紀錄」提醒。

## 常用參數

```bash
# 要 5 支 30 秒的，語氣輕鬆一點
ytreel <url> --reels 5 --seconds 30 --tone "像跟朋友講話，不要說教"

# 指定目標觀眾
ytreel <url> --audience "25-35 歲剛開始投資的人"

# 影片有 CC 字幕，但你覺得品質很差，想改用語音辨識
ytreel <url> --prefer-asr

# 需要登入才能看的影片
ytreel <url> --cookies-from-browser chrome

# 用本機已經有的字幕檔或音訊檔，不下載
ytreel <url> --subtitle-file my.vtt
ytreel <url> --audio-file my.m4a

# 從 YouTube「顯示轉錄稿」複製存成 .txt（要保留時間碼）
ytreel <url> --subtitle-file transcript.txt

# 不叫模型，只輸出逐字稿與提示詞包（prompt_system.txt / prompt_user.md / prompt_schema.json）
ytreel <url> --llm none
```

完整參數：`ytreel --help`

## 執行環境需要的網路權限

| 用途 | 網域 |
| --- | --- |
| 抓字幕與音訊 | `youtube.com`、`*.youtube.com`、`*.googlevideo.com` |
| 呼叫模型（`--llm api`） | `api.anthropic.com` |
| 下載 Whisper 模型（第一次語音辨識） | `huggingface.co`、`*.huggingface.co`、`*.hf.co` |

設定允許清單時要注意**子網域**：實際連線的是 `www.youtube.com`、
`rr1---sn-xxx.googlevideo.com`（影片串流）、`us.aws.cdn.hf.co`（模型檔）這類主機，
只寫 `youtube.com` 是不會放行它們的，要寫成 `*.youtube.com`。

網路被擋時 `yt-dlp` 會出現 `Tunnel connection failed: 403`，工具會直接說明是網路政策問題。

## YouTube 說「請確認你不是機器人」

在雲端主機、資料中心或 VPN 上執行時，YouTube 常會把流量判定成機器人，要求登入。
這跟網路權限無關 —— 連得到 YouTube，但它拒絕給資料。工具遇到時會明確告訴你，
並提醒：**這種狀態下拿不到字幕清單，所以「沒有找到字幕」不一定是真的沒有。**

解法（擇一）：

1. **換到一般家用網路執行**。最可靠。
2. **貼逐字稿**：在 YouTube 影片說明欄點「顯示轉錄稿」，全選複製存成 `.txt`，
   用 `--subtitle-file transcript.txt` 匯入。時間碼要保留，報告裡的連結全靠它。
3. **`--cookies-from-browser chrome`**：用瀏覽器的 YouTube 登入狀態。這等於把帳號
   的登入權限交給 yt-dlp，建議用分身帳號 —— YouTube 可能把用來下載的帳號標記為異常。

## 設計上的幾個選擇

- **人工 CC 字幕一律優先於自動字幕**，就算語言偏好順序較後面也一樣 —— 人工字幕沒有辨識錯誤，
  品質差距比語言差距大得多。
- **「要不要校對」看內容而不是看標籤**。有些標為「人工」的字幕其實是機器產的，所以用標點密度
  判斷：幾乎沒有標點就當成機器字幕處理。
- **時間碼切得細**（預設每句上限 60 字，約每 7-15 秒一個時間碼），模型才引用得準。可用
  `--merge-chars` 調整。
- **長影片走 map-reduce**：超過 `--max-direct-chars`（預設 25 萬字）才先做分段筆記再彙整，
  彙整時仍附上逐字稿頭尾節錄，讓腳本抓得到講者真正的用語。
- **模型回來的結果會做健檢**：腳本數量、論點是否重複、時間碼有沒有超出影片長度、
  口播字數與預估秒數是否對得上。這些是提醒而不是錯誤，不會丟掉已經產出的成果。

## 開發

```bash
pip install -e '.[dev,all]'
pytest                      # 141 個測試，全部離線
ruff check .                # lint
```

測試不需要網路也不需要 API key：模型呼叫用假 client，YouTube 用字幕檔 fixture。
每次 push 到 main 與每個 PR 都會在 Python 3.10 / 3.12 上自動跑一次。
