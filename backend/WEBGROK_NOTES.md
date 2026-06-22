# MarsRadar —— web 版 Grok 讀 X 後端（`elon_digest_webgrok.py`）

## 為什麼有這支

原本 `elon_digest.py` 用 **Grok Build CLI**（`grok -p ...`）讀 X。CLI 讀 X 的快路徑
全壞了（Chrome cookie 鎖、bird 拿不到 cookie），只能退到慢 nitter → cron 反覆逾時。

實測「瀏覽器上已登入的 **web 版 grok（grok.com）**」有原生 X 存取，約 **90 秒**就能讀回
結構化的馬斯克推文 JSON。`elon_digest_webgrok.py` 就是把「讀 X 的來源」從 CLI 換成
**驅動常駐瀏覽器上的 grok.com 分頁**，其餘（合併、寫檔、push）完全沿用 `elon_digest.py`。

## 它跟 `elon_digest.py` 的關係

- **只換掉讀 X 這一段**。schema、`normalize_items`、`merge_daily_items`、`write_digest`、
  `rebuild_index`、`git_commit_push`、`dump_public`/`load_public`、`load_dotenv`
  全部 `import elon_digest as ed` 直接重用，產出格式 100% 一致（digests/<date>.json +
  latest.json + index.json，UTC+8 台北切檔，可選加密）。
- App 端不用改：讀回去的 JSON 結構跟 CLI 版相同。

## 執行前置條件（很重要，這支不會自己幫你準備）

1. **browser MCP daemon 已在跑**，預設 `http://127.0.0.1:3457`（SSE）。
   - 這是 imagegen 工具用的同一支 daemon（`20260530-imagegen-cli-ext/mcp_client.py`
     就是打它）。
   - 驗證：`python3 /mnt/d/claude_agent/20260530-imagegen-cli-ext/mcp_client.py list_tabs`
     有列出分頁＝daemon 活著。
2. **瀏覽器（含 Claude Browser Agent 擴充功能）已開、且已登入 grok.com**，並確認該帳號
   在 grok.com 真的有 X 原生存取（手動在 grok 問「列出 elonmusk 最近的貼文」會回真資料）。
   - 預設會**複用**已開的 grok.com 分頁（`WEBGROK_REUSE_TAB=1`）；沒有就開新分頁。

## 怎麼跑

```bash
# 只 commit、不 push（預設，安全）
python3 /mnt/d/claude_agent/T-172-marsradar-elon-digest/public-repo/backend/elon_digest_webgrok.py

# 真的 push 到公開 repo
GIT_PUSH=1 python3 .../backend/elon_digest_webgrok.py
```

### 環境變數

| 變數 | 預設 | 說明 |
|------|------|------|
| `MARSRADAR_MCP_BASE` | `http://127.0.0.1:3457` | browser MCP daemon base URL |
| `WEBGROK_LOOKBACK_HOURS` | `12` | 要 grok 回顧幾小時的 X 活動 |
| `WEBGROK_GEN_TIMEOUT` | `240` | 等 grok 讀 X + 生成完成的逾時秒數 |
| `WEBGROK_REUSE_TAB` | `1` | `1`＝找現有 grok.com 分頁複用；`0`＝一律開新分頁 |
| `DIGEST_REPO_DIR` | 後端的上一層 | 資料倉庫本機路徑（同 `elon_digest.py`） |
| `GIT_PUSH` | `0` | `1` 才真的 push（同 `elon_digest.py`） |
| `MARSRADAR_ENC_KEY` | （未設） | 設了就把公開 JSON 加密寫出（同 `elon_digest.py`） |

### 失敗行為

- 連不到 daemon / grok 編輯器沒就緒 / 送出失敗 / 等不到回覆 → 印清楚錯誤、`exit 2`。
- 寫檔/合併/push 階段出錯 → `exit 3`。
- grok 回空陣列（窗口內 Elon 真的沒發文）→ **不算錯**，照常寫出（brief 標 "No new …"）。

## 核心流程（程式內部）

1. `MCP().connect()` 連 browser daemon（SSE → initialize）。
2. `GrokTab.open()` 找/開 grok.com 分頁；`wait_editor()` 等 `.tiptap.ProseMirror` 出現。
3. `send(prompt)`：
   - 聚焦 `.tiptap.ProseMirror` → `browser_fill clear=false`（TipTap 鐵律，逐字輸入才會
     觸發 React onChange；JS 注入/execCommand/paste 都會送出空字串）。
   - 點提交鈕 `button[aria-label='提交']`（**grok 按 Enter 不送出**），重試到輸入框清空。
4. `wait_reply()`：輪詢「沒有停止鈕（不在生成中）＋ code block 內容連續兩次穩定」判完成，
   抓最後一則回覆裡最像 JSON 陣列的 `pre code` 文字。
   - grok.com 的 CDP eval 回傳偶爾為 null（已知坑）→ 退路把結果塞 `document.title`
     再用 `browser_get_page_info` 讀 title 取回（雙保險）。
5. `parse_grok_array()` 抽出 JSON 陣列（容忍 ```json fence、前後雜訊、`{"posts":[...]}` 包裝）。
6. `grok_items_to_digest()` 對映成 digest schema：
   - `type` → `source_type`（post/reply/quote/repost → musk_post/musk_reply/musk_quote）。
   - `topic`+`text` 關鍵字 → `category`（tesla/spacex/xai_x_platform/elon_personal）。
   - `title_en`＝topic 骨幹、`summary_en`＝text 濃縮、`musk_quote`＝貼文原文、
     `links`＝`[{label:"Elon on X", url:link}]`、`importance` 沿用（clamp 1–5）。
   - `story_id`＝link 的 `/status/<id>`（抓不到才用 topic slug）。
7. 重用 `ed.write_digest` → `ed.rebuild_index` → `ed.git_commit_push`。

## 待補（中文欄位）

目前 `title_zh / summary_zh / musk_quote_zh / brief_zh` 先**留空字串**（`write_digest`
容許空字串、App 端可 fallback 顯示英文）。後續若要中文，可在 `grok_items_to_digest`
之後接一段翻譯（再問一次 grok、或本機 LLM）填回這四個欄位，不影響其餘流程。

## cron 怎麼接（先別改，只記步驟）

⚠️ **這支跟 CLI 版的關鍵差異**：它需要「一個有 GUI、開著瀏覽器並登入 grok.com、且
browser MCP daemon 在跑」的環境。**不能丟 headless / GitHub Actions 雲端跑**（雲端沒有
你的瀏覽器登入態與 X 存取）。所以 cron 必須跑在「平常那台開著 Chrome + 擴充功能的機器」。

之後要接時（**現在先不要動現有 cron**）：

1. 確認那台機器**開機就會**：(a) 起 browser MCP daemon、(b) 開瀏覽器並停在登入好的
   grok.com 分頁。（daemon 自啟見既有 imagegen 工具的設定；瀏覽器登入態靠 Chrome profile。）
2. 仿照 `run.sh` 做一支 `run_webgrok.sh`（或在 `run.sh` 加分支），把 `exec` 那行改成跑
   `elon_digest_webgrok.py`，並設好 `GIT_PUSH=1`、`WEBGROK_GEN_TIMEOUT`（grok 偶爾較慢可拉到 300）。
3. crontab 仿 `crontab.example`：`5 */2 * * * /path/to/run_webgrok.sh`，但務必跑在
   **同一個有瀏覽器登入態的桌面 session**（不是純背景使用者）。
4. 切換策略建議：先**並存**——保留現有 CLI 版 cron，新增 webgrok 版手動/低頻跑幾天觀察
   產出穩定後，再把主 cron 換成 webgrok 版、停掉 CLI 版。

## 已知坑 / 注意

- **TipTap 輸入**：一定要 `browser_fill clear=false`；`clear=true` 或 JS 注入會破壞
  ProseMirror 內部 state，送出空字串。
- **送出**：grok 按 Enter 不送出，一定要點 `button[aria-label='提交']`。
- **CDP eval 回傳**：grok.com 上偶爾回 null，本支已用 `document.title` 側通道做退路。
- **生成中判定**：靠「停止」鈕（`button[aria-label='停止'/'Stop']`）是否存在；grok 改版若
  改了 aria-label，`_generating()` 要跟著更新（目前涵蓋 停止/Stop/停止生成）。
- **不要並行**：同一個 grok.com 分頁不要同時被別的工具搶（會互相打斷對話）。
