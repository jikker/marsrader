#!/usr/bin/env python3
from __future__ import annotations

"""
MarsRadar — Elon / Tesla / SpaceX / X 動態聚合後端
=================================================

每 2 小時跑一次：
  1. 呼叫 Grok 讓它用 Live Search 監控特定 X 帳號 + 網路新聞
  2. 讓 Grok 整理成「中英重點 + 分類 + 原始連結」的結構化 JSON
  3. 合併進當天的 digests/<date>.json，更新 index.json / latest.json
  4. git commit & push 到公開資料庫（iOS App 直接讀 raw JSON）

== 兩種後端（DIGEST_BACKEND 切換）==
  cli（預設，推薦）  呼叫本機 **Grok Build CLI**（`grok -p ...`，吃你的 Grok 訂閱、
                    不需要任何 API key）。CLI 自己找路徑讀 X / web，回傳結構化 JSON。
  api               呼叫 xAI / Grok REST API（https://api.x.ai），需 XAI_API_KEY（pay-per-use）。

設計重點（呼應上架/成本兩大地雷）：
  - 只「監控特定清單」（Elon 本人 + Tesla/SpaceX 官方 + 幾位科技記者），不盲撈全網 → 控制成本
  - 後端把資料整理、快取成結構化 JSON，App 端只讀 JSON，永遠不直接打 X API
  - 內容是「自動整理 + 附原連結出處」，不複製整篇新聞原文 → 合理使用

環境變數（放 .env 或系統環境）：
  DIGEST_BACKEND     選填，cli（預設）或 api
  --- cli 後端 ---
  GROK_BIN           選填，Grok CLI 執行檔路徑（預設自動找 PATH / ~/.grok/bin/grok）
  GROK_MODEL         選填，傳給 grok --model（不設＝CLI 預設 grok-build 模型）
  GROK_TIMEOUT       選填，單次呼叫逾時秒數（預設 600）
  GROK_RETRY_TIMEOUT 選填，降級重試的單次逾時秒數（預設 600）
  GROK_RETRY_ON_TIMEOUT 選填，"0" 可關閉 CLI timeout 後的降級重試
  --- api 後端 ---
  XAI_API_KEY        api 後端必填，xAI / Grok API key（https://console.x.ai）
  XAI_MODEL          選填，預設 grok-4（可改 grok-4-fast 省錢）
  --- 共用 ---
  DIGEST_REPO_DIR    選填，資料倉庫本機路徑（預設＝後端的上一層，digests/ 所在處）
  GIT_PUSH           選填，"1" 才真的 git push（預設只 commit，不 push）
  MARSRADAR_ENC_KEY  選填，**設了才會把 index.json / latest.json / digests/*.json 用
                     AES-256-GCM 加密後再寫出**（64 hex 字元＝32 bytes 金鑰）。
                     沒設＝照舊寫明文（向後相容）。App 端內嵌同一把金鑰自動解密。
                     用途：擋住「直接用瀏覽器/curl 開公開 JSON 看歷史」的非技術使用者
                     （能擋約 8 成；真正懂技術的人 dump App binary 仍可取出金鑰，這是
                     公開 URL 架構的本質限制，已與用戶確認接受、不另做 server-side gating）。
                     ⚠️ 切換時機：等「內建解密的新 App build」上架後，再把這把金鑰寫進
                     .env（並設為 GitHub Actions secret）啟用，以免現役 App 讀不到。
"""
import os
import sys
import json
import base64
import re
import shutil
import tempfile
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

# ---------------------------------------------------------------- 設定 ----

ROOT = Path(__file__).resolve().parent
# 資料倉庫＝本後端的「上一層」（digests/、index.json、latest.json 都直接放在那）。
# GitHub Actions 會用 DIGEST_REPO_DIR 覆蓋；自架 cron（方式 B）不設時就用這個預設值。
DEFAULT_REPO = ROOT.parent.resolve()

DIGEST_BACKEND = os.environ.get("DIGEST_BACKEND", "cli").strip().lower()
XAI_API_KEY = os.environ.get("XAI_API_KEY", "").strip()
XAI_MODEL = os.environ.get("XAI_MODEL", "grok-4").strip()
REPO_DIR = Path(os.environ.get("DIGEST_REPO_DIR", str(DEFAULT_REPO))).resolve()
GIT_PUSH = os.environ.get("GIT_PUSH", "0") == "1"

# 公開 JSON 加密（見頂部 docstring 的 MARSRADAR_ENC_KEY）。env 在 load_dotenv 後才有值，
# 所以實際讀取放在 _enc_key()，這裡不快取。
ENC_ENVELOPE_TAG = "marsradar_enc"

XAI_ENDPOINT = "https://api.x.ai/v1/chat/completions"

# 只監控馬斯克本人（2026-07-12 用戶指示：不額外監控其他 ID；轉推 repost 也納入監控範圍）。
# 新聞/web 僅作佐證，不再監控其他 X 帳號。
WATCH_HANDLES = [
    "elonmusk",      # Elon 本人（含轉推）
]

CATEGORIES = ["elon_personal", "tesla", "spacex", "xai_x_platform", "other"]


def env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default

# ----------------------------------------------------------- Prompt 共用 ----

SYSTEM_RULES = """You are MarsRadar's editor. You produce a concise, accurate, bilingual
(English + Traditional Chinese / 繁體中文) digest of what Elon Musk is actually saying and
doing on X, plus related Tesla / SpaceX / xAI / X-platform developments.

SOURCE PRIORITY (strict — this is the core of the product):
1) PRIMARY (the ONLY monitored X account) = @elonmusk's OWN X activity in the window:
   original posts, REPOSTS/RETWEETS, quote-posts and substantive replies (not @-only spam).
   Capture his ACTUAL words, tone and topics. Reposts/retweets COUNT as his activity: report
   WHAT he amplified and WHO wrote it. This is the main product — lead with "what Elon
   said/amplified", not with a news headline.
2) SECONDARY = Reputable news / web: background or corroboration ONLY for stories Musk posted,
   reposted or replied about. Do NOT monitor any other X account. Never let a news headline
   become the main story if Elon already posted about the same topic.

ANTI-HALLUCINATION:
- Use ONLY facts found via live X reading and web search. Do NOT invent posts, quotes or URLs.
- Every link must be a real URL you actually retrieved; prefer x.com/... post permalinks.
- NEVER copy a full article or full tweet thread. Paraphrase in your OWN 1-3 sentences (fair use).
- Respect timestamps: only include items from the stated lookback window; do NOT resurface old
  viral posts as if new. If you cannot verify a claim or URL, omit it.

X SOURCE ACCURACY (strict — fixes wrong "Elon on X" labels):
- For every X status URL, identify the ACTUAL author of that exact status id. Do not infer the
  author from the surrounding conversation or topic.
- If source_type starts with musk_, links[0] MUST be the actual @elonmusk status URL for his post,
  quote-post, or reply. Do not use the URL of the post he replied to / quoted as the primary link.
  EXCEPTION musk_repost: a plain repost/retweet has NO separate permalink — links[0] is the
  ORIGINAL author's status URL with elon_relation=reposted, the label must name the original
  author, and the title must make clear Elon reposted it.
- If you include a post by someone else because Elon replied to or quoted it, label it as that
  author's post and set elon_relation to replied_to / quoted / reposted / mentioned / none.
- Never label a non-@elonmusk status as "Elon on X" or "Elon Musk on X".
- When unsure whether a URL is Elon's own status, set source_type=mixed or news and explain via
  summary instead of pretending it is a musk_post.

SAME-DAY STORY MERGE (critical — avoid duplicates):
- You will receive EXISTING_ITEMS for today (UTC+8 / Taipei calendar day). Treat them as the canonical list so far.
- ONE real-world story = ONE item for the whole day. When new info arrives, UPDATE the matching
  item in place (keep its story_id, preserve first_seen, refresh summaries/importance/links/
  musk_quote/updated_at). Only create a NEW item if it is genuinely a different story.
- Do NOT merge two different stories just because they involve the same company/topic.
- Do NOT delete an existing item unless it was proven false. Return the FULL merged day list.
- Rebuild brief_en / brief_zh from the merged full-day picture, not just the newest window.

OUTPUT:
- Categorize each item: elon_personal | tesla | spacex | xai_x_platform | other
- Rate importance 1 (minor) to 5 (major); Elon's own posts on major topics = 4-5.
- Bilingual title + summary for every item.
- Top-level one-minute brief in EN and ZH (一分鐘看懂今日動態)."""

SCHEMA_BLOCK = """Return STRICT JSON with EXACTLY this shape:
{{
  "brief_en": "<=60 words, full-day one-minute brief",
  "brief_zh": "<=60字 一分鐘看懂今日動態（整日合併後）",
  "items": [
    {{
      "story_id": "stable-lowercase-kebab-id-for-this-story-today (e.g. tesla-fsd-china-2026-06-22)",
      "category": "elon_personal|tesla|spacex|xai_x_platform|other",
      "source_type": "musk_post|musk_reply|musk_quote|musk_repost|official_account|news|mixed",
      "title_en": "...",
      "title_zh": "...",
      "summary_en": "1-3 sentences, your own words; lead with what Musk said if applicable",
      "summary_zh": "1-3 句，用你自己的話；若為馬斯克發文，先寫他說了什麼",
      "musk_quote": "short verbatim excerpt of Elon's own words (<=280 chars); empty string if not a musk_* item",
      "musk_quote_zh": "馬斯克原話的繁中翻譯；非本人發文則空字串",
      "importance": 1,
      "first_seen": "ISO-8601 UTC — PRESERVE from the existing item if you are updating it",
      "updated_at": "ISO-8601 UTC — now",
      "links": [
        {{
          "label": "Exact source label, e.g. Elon on X / Nick Shirley on X / Reuters",
          "url": "https://x.com/<actual_author>/status/<status_id> or https://...",
          "x_author_name": "Display name of the exact X status author, empty for non-X links",
          "x_author_handle": "handle of the exact X status author without @, empty for non-X links",
          "elon_relation": "own_post|reply_by_elon|quote_by_elon|replied_to|quoted|reposted|mentioned|none"
        }}
      ]
    }}
  ]
}}
Rules:
- musk_quote required (non-empty) for musk_post/musk_reply/musk_quote; for musk_repost put the
  reposted post's key sentence (it is what Elon chose to amplify); use "" for pure news items.
- links[0] should be the PRIMARY source (the actual @elonmusk status URL when source_type is
  musk_post/musk_reply/musk_quote; the ORIGINAL author's status URL for musk_repost).
- For X links, x_author_handle MUST match the actual author of that exact status id; if it is not
  elonmusk, the label must not say Elon.
- items = the FULL merged list for today AFTER applying EXISTING_ITEMS (not a delta, not just new ones).
- Aim for 6-15 high-signal items for the whole day. Drop stale low-value noise."""

USER_TEMPLATE = """Today is {date} (UTC+8 / Taipei time). Current run time (UTC): {run_iso}.

TASK: Produce today's MarsRadar digest by MERGING new developments from the LAST {lookback_hours}
HOURS into any existing items for this calendar day (UTC+8 / Taipei). Lead with @elonmusk's own posts.

=== EXISTING_ITEMS (today so far — UPDATE/MERGE these in place, do NOT duplicate stories) ===
{existing_items_json}

=== ACCOUNTS TO MONITOR ===
ONLY @elonmusk — his original posts, REPOSTS/RETWEETS, quote-posts and substantive replies.
Do NOT monitor any other X account. Reputable news/web may be used ONLY to corroborate or add
context to stories Musk himself posted/reposted/replied about.

=== WHAT TO PRIORITIZE ===
1. What did @elonmusk literally say/post in the window? (topic, tone, controversy, product hints,
   politics) — group related posts into ONE story item, not one item per reply.
2. What did he REPOST/RETWEET? Reposts are part of the product — report what he amplified
   (original author + content) as musk_repost items.
3. Skip: reply spam, engagement bait with no substance, stale reposts of old news resurfacing.

""" + SCHEMA_BLOCK


def _compact_existing(items: list, limit: int = 25) -> list:
    """精簡今日既有條目再餵給 Grok：只留辨識同故事所需欄位（id/標題/分類/重要度/英文摘要/首見時間），
    丟掉 links、zh、musk_quote 等大欄位 → 控制 prompt 大小、避免 Grok 逾時。
    依重要度取前 limit 筆（低訊號舊條目不必回灌）。"""
    ranked = sorted(items, key=lambda x: -int(x.get("importance", 3)))[:limit]
    return [{
        "story_id": it.get("story_id") or it.get("id"),
        "category": it.get("category"),
        "importance": it.get("importance", 3),
        "title_en": it.get("title_en", ""),
        "summary_en": (it.get("summary_en", "") or "")[:240],
        "first_seen": it.get("first_seen", ""),
    } for it in ranked]


def build_prompt(date_str: str, run_iso: str = "", existing_items: list | None = None,
                 lookback_hours: int = 12, existing_limit: int = 25) -> str:
    """組出單一段提示詞（CLI 用一段、API 拆 system/user 各用一半）。
    existing_items：今日已存在的條目（供 Grok 演進式合併，避免同日重複）。"""
    existing_json = (json.dumps(_compact_existing(existing_items, limit=existing_limit),
                                ensure_ascii=False, indent=1)
                     if existing_items else "[]")
    user = USER_TEMPLATE.format(date=date_str, run_iso=run_iso or date_str,
                                lookback_hours=lookback_hours,
                                existing_items_json=existing_json)
    return (SYSTEM_RULES + "\n\n" + user
            + "\n\nOutput STRICT JSON only — no markdown fences, no commentary before or after.")


def extract_json_object(text: str) -> dict:
    """從可能含前後雜訊（preamble、```fence）的文字中抽出第一個完整 JSON 物件。"""
    text = text.strip()
    # 去掉 ```json fence（若有）
    if text.startswith("```"):
        text = text[3:]
        if text.startswith("json"):
            text = text[4:]
        if text.startswith("\n"):
            text = text[1:]
        end_fence = text.rfind("```")
        if end_fence != -1:
            text = text[:end_fence]
        text = text.strip()
    # 找第一個 '{'，用括號計數（略過字串內容）找到對應的 '}'
    start = text.find("{")
    if start == -1:
        raise ValueError("回應中找不到 JSON 物件起始 '{'")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("回應中的 JSON 物件未正確閉合")


# ------------------------------------------------------ Grok CLI 後端 ----

def _resolve_grok_bin() -> str:
    cand = os.environ.get("GROK_BIN", "").strip()
    if cand and Path(cand).exists():
        return cand
    found = shutil.which("grok")
    if found:
        return found
    home_bin = Path.home() / ".grok" / "bin" / "grok"
    if home_bin.exists():
        return str(home_bin)
    raise RuntimeError(
        "找不到 Grok CLI。請先安裝（curl -fsSL https://x.ai/cli/install.sh | bash）"
        "或設定環境變數 GROK_BIN=/path/to/grok。"
    )


def call_grok_cli(date_str: str, run_iso: str = "", existing_items: list | None = None) -> dict:
    """呼叫本機 Grok Build CLI（headless），讓它讀 X/web 後回傳結構化 JSON。
    不需要任何 API key，吃的是使用者的 Grok 訂閱。"""
    grok_bin = _resolve_grok_bin()
    timeout = env_int("GROK_TIMEOUT", 600)
    retry_timeout = env_int("GROK_RETRY_TIMEOUT", min(timeout, 600))
    model = os.environ.get("GROK_MODEL", "").strip()
    attempts = [(12, 25, timeout)]
    if os.environ.get("GROK_RETRY_ON_TIMEOUT", "1") != "0":
        attempts.extend([(6, 12, retry_timeout), (3, 6, retry_timeout)])

    timeout_notes = []
    for attempt_no, (lookback_hours, existing_limit, attempt_timeout) in enumerate(attempts, 1):
        prompt = build_prompt(date_str, run_iso, existing_items,
                              lookback_hours=lookback_hours,
                              existing_limit=existing_limit)

        # CLI 會載入 cwd 的 .mcp.json（含需 OAuth 的 server 會卡死）→ 用乾淨臨時目錄當 cwd。
        workdir = tempfile.mkdtemp(prefix="marsradar_grok_")
        prompt_file = Path(workdir) / "prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")

        cmd = [grok_bin, "--cwd", workdir, "--always-approve",
               "--output-format", "json", "--prompt-file", str(prompt_file)]
        if model:
            cmd += ["--model", model]

        try:
            proc = subprocess.run(cmd, capture_output=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=attempt_timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            note = (f"attempt {attempt_no} timeout: "
                    f"lookback={lookback_hours}h existing_limit={existing_limit} "
                    f"timeout={attempt_timeout}s")
            timeout_notes.append(note)
            print(f"[grok] {note}; retrying with smaller prompt" if attempt_no < len(attempts)
                  else f"[grok] {note}")
            continue
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        if proc.returncode != 0:
            raise RuntimeError(
                f"Grok CLI 失敗 (exit {proc.returncode})：{(proc.stderr or proc.stdout)[:500]}")

        # CLI 的 --output-format json 外層 envelope：{"text","stopReason","sessionId","requestId","thought"}
        try:
            envelope = json.loads(proc.stdout.strip())
            inner_text = envelope.get("text", "")
        except json.JSONDecodeError:
            # 萬一沒拿到 envelope，就把整段 stdout 當內容處理
            envelope = {}
            inner_text = proc.stdout

        data = extract_json_object(inner_text)
        data["_usage"] = {
            "backend": "grok-cli",
            "model": model or "grok-build",
            "requestId": envelope.get("requestId", ""),
            "stopReason": envelope.get("stopReason", ""),
            "attempt": attempt_no,
            "lookback_hours": lookback_hours,
            "existing_limit": existing_limit,
            "timeout_notes": timeout_notes,
        }
        return data

    raise RuntimeError("Grok CLI 逾時；已嘗試降級重試仍未完成：" + " | ".join(timeout_notes))


# --------------------------------------------------------- Grok API 後端 ----

def call_grok_api(date_str: str, run_iso: str = "", existing_items: list | None = None) -> dict:
    """（備援）呼叫 xAI Grok REST API，啟用 Live Search，回傳解析後的 dict。需 XAI_API_KEY。"""
    try:
        import requests
    except ImportError:
        sys.exit("api 後端需要 requests：請先 pip install requests（或改用預設的 cli 後端）")

    if not XAI_API_KEY:
        raise RuntimeError(
            "api 後端缺少 XAI_API_KEY。請到 https://console.x.ai 申請後寫入 .env，"
            "或改用預設的 cli 後端（DIGEST_BACKEND=cli，吃 Grok 訂閱、不需 key）。"
        )

    existing_json = (json.dumps(_compact_existing(existing_items), ensure_ascii=False, indent=1)
                     if existing_items else "[]")
    user_msg = USER_TEMPLATE.format(date=date_str, run_iso=run_iso or date_str,
                                    lookback_hours=12, existing_items_json=existing_json)
    payload = {
        "model": XAI_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_RULES},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "search_parameters": {
            "mode": "on",
            # 抓整個 UTC 當日，靠 EXISTING_ITEMS 合併補全天脈絡（不只 12h）。
            "from_date": date_str,
            "max_search_results": 30,
            "sources": [
                # 只監控馬斯克本人（含轉推）；新聞/網路只當佐證，不監控其他 X 帳號。
                {"type": "x", "x_handles": WATCH_HANDLES},
                {"type": "news"},
                {"type": "web"},
            ],
        },
    }

    resp = requests.post(
        XAI_ENDPOINT,
        headers={"Authorization": f"Bearer {XAI_API_KEY}",
                 "Content-Type": "application/json"},
        json=payload,
        timeout=180,
    )
    resp.raise_for_status()
    body = resp.json()
    content = body["choices"][0]["message"]["content"]
    data = extract_json_object(content)
    usage = body.get("usage", {})
    usage["backend"] = "grok-api"
    usage["model"] = XAI_MODEL
    data["_usage"] = usage
    return data


def call_grok(date_str: str, run_iso: str = "", existing_items: list | None = None) -> dict:
    """依 DIGEST_BACKEND 選擇後端。預設 cli（吃訂閱、不需 key）。
    existing_items：今日已存在條目，餵回給 Grok 做演進式合併（避免同日重複）。"""
    if DIGEST_BACKEND == "api":
        return call_grok_api(date_str, run_iso, existing_items)
    return call_grok_cli(date_str, run_iso, existing_items)


# ------------------------------------------------------ 公開 JSON 加密 ----

def _enc_key() -> bytes | None:
    """讀 MARSRADAR_ENC_KEY（hex）。沒設＝回 None（寫明文，向後相容）。"""
    h = os.environ.get("MARSRADAR_ENC_KEY", "").strip()
    if not h:
        return None
    try:
        key = bytes.fromhex(h)
    except ValueError:
        raise RuntimeError("MARSRADAR_ENC_KEY 必須是 hex 字串（64 字元）")
    if len(key) != 32:
        raise RuntimeError(f"MARSRADAR_ENC_KEY 需為 32 bytes（64 hex 字元），目前 {len(key)} bytes")
    return key


def encrypt_text(text: str, key: bytes) -> str:
    """AES-256-GCM 加密 → 回傳信封 JSON 字串。
    blob = base64(nonce[12] + ciphertext + tag[16])，與 CryptoKit 的
    AES.GCM.SealedBox(combined:) 位元組順序一致，App 端可直接解。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, text.encode("utf-8"), None)  # ct 尾端含 16-byte tag
    blob = base64.b64encode(nonce + ct).decode("ascii")
    return json.dumps({ENC_ENVELOPE_TAG: 1, "alg": "AES-256-GCM", "blob": blob},
                      ensure_ascii=False)


def dump_public(path: Path, obj: dict):
    """把要供 App 讀取的公開 JSON 寫出。有金鑰就加密成信封，沒有就寫明文。"""
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    key = _enc_key()
    if key is None:
        path.write_text(text, encoding="utf-8")
    else:
        path.write_text(encrypt_text(text, key), encoding="utf-8")


def decrypt_text(envelope_text: str, key: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    env = json.loads(envelope_text)
    raw = base64.b64decode(env["blob"])
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")


def load_public(path: Path) -> dict:
    """讀回 dump_public 寫出的檔：自動辨識「加密信封 / 明文」並回傳 dict。
    讓同日重跑合併、rebuild_index 在已加密的倉庫上也能正常運作。"""
    text = path.read_text(encoding="utf-8")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        raise
    if isinstance(obj, dict) and obj.get(ENC_ENVELOPE_TAG) and obj.get("blob"):
        key = _enc_key()
        if key is None:
            raise RuntimeError(
                f"{path.name} 是加密檔，但未設 MARSRADAR_ENC_KEY，無法讀回合併。")
        return json.loads(decrypt_text(text, key))
    return obj


# --------------------------------------------------------- JSON 寫入/合併 ----

SOURCE_TYPES = {"musk_post", "musk_reply", "musk_quote", "musk_repost",
                "official_account", "news", "mixed", "web"}


def _slugify_story(it: dict, idx: int) -> str:
    """沒給 story_id 時，從 title_en 生一個穩定 fallback id。"""
    base = (it.get("title_en") or "").lower()
    slug = "".join(c if c.isalnum() else "-" for c in base).strip("-")
    slug = "-".join(p for p in slug.split("-") if p)[:60]
    return slug or f"item-{idx}"


GENERIC_X_LABELS = {"elon on x", "elon musk on x", "elon", "x"}


def _compact_link_label_part(text: str, max_len: int = 42) -> str:
    text = re.sub(r"https?://\S+", "", text or "")
    text = re.sub(r"\s+", " ", text).strip(" -—:：。.,，")
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip(" -—:：。.,，") + "…"


def _url_status_suffix(url: str) -> str:
    m = re.search(r"/status/(\d+)", url or "")
    return m.group(1)[-6:] if m else ""


def _x_status_id(url: str) -> str:
    m = re.search(r"/status/(\d+)", url or "")
    return m.group(1) if m else ""


def _x_handle_from_url(url: str) -> str:
    try:
        parsed = urlparse(url)
    except Exception:
        return ""
    host = re.sub(r"^www\.", "", parsed.netloc.lower())
    if host not in ("x.com", "twitter.com"):
        return ""
    parts = [p for p in parsed.path.split("/") if p]
    if not parts or parts[0].lower() == "i":
        return ""
    return parts[0].lstrip("@")


def _x_author_label(author_name: str, author_url: str) -> str:
    handle = _x_handle_from_url(author_url).lower()
    if handle == "elonmusk":
        return "Elon on X"
    name = (author_name or "").strip()
    if name:
        return f"{name} on X"
    if handle:
        return f"@{handle} on X"
    return "X"


def _x_author_label_from_fields(author_name: str, author_handle: str) -> str:
    handle = (author_handle or "").strip().lstrip("@")
    name = (author_name or "").strip()
    if handle.lower() == "elonmusk":
        return "Elon on X"
    if name:
        return f"{name} on X"
    if handle:
        return f"@{handle} on X"
    return ""


def _canonicalize_x_link(link: dict, cache: dict[str, dict]) -> None:
    """Use X oEmbed to correct wrong /{handle}/status/{id} paths returned by LLMs."""
    url = (link.get("url") or "").strip()
    sid = _x_status_id(url)
    if not sid:
        return
    if sid not in cache:
        cache[sid] = {}
        oembed = "https://publish.twitter.com/oembed?omit_script=1&url=" + quote(
            f"https://twitter.com/i/status/{sid}",
            safe="",
        )
        try:
            req = Request(oembed, headers={"User-Agent": "MarsRadar/1.0"})
            with urlopen(req, timeout=8) as resp:
                cache[sid] = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            print(f"  ⚠️ X oEmbed canonicalize failed for {sid}: {type(exc).__name__}: {exc}")
            return
    data = cache.get(sid) or {}
    canonical_url = (data.get("url") or "").strip()
    author_url = (data.get("author_url") or "").strip()
    author_name = (data.get("author_name") or "").strip()
    if canonical_url:
        link["url"] = canonical_url
    if author_url or author_name:
        link["_x_author_label"] = _x_author_label(author_name, author_url)


def _host_label(url: str) -> str:
    try:
        host = re.sub(r"^www\.", "", urlparse(url).netloc.lower())
    except Exception:
        return "Source"
    if host in ("x.com", "twitter.com"):
        handle = _x_handle_from_url(url).lower()
        if handle == "elonmusk":
            return "Elon on X"
        if handle:
            return f"@{handle} on X"
        return "X"
    return host or "Source"


def _normalize_link_label(it: dict, link: dict, idx: int) -> str:
    label = (link.get("label") or link.get("source") or "").strip()
    url = (link.get("url") or "").strip()
    author_label = (link.get("_x_author_label") or "").strip()
    if not author_label:
        author_label = _x_author_label_from_fields(
            link.get("x_author_name") or link.get("author_name") or "",
            link.get("x_author_handle") or link.get("author_handle") or "",
        )
    label_l = label.lower()
    author_l = author_label.lower()
    elon_label_for_non_elon = (
        bool(author_label)
        and not author_l.startswith("elon ")
        and (label_l.startswith("elon on x") or label_l.startswith("elon musk on x"))
    )
    if label and label_l not in GENERIC_X_LABELS and not elon_label_for_non_elon:
        return label

    base = author_label or _host_label(url)
    details = []
    relation = (link.get("elon_relation") or "").strip().lower()
    source_type = (it.get("source_type") or "").strip()
    if relation in ("reply_by_elon", "replied_to"):
        details.append("reply")
    elif relation in ("quote_by_elon", "quoted"):
        details.append("quote")
    elif relation == "reposted":
        details.append("repost")
    elif source_type in ("musk_reply", "musk_quote", "musk_repost"):
        details.append({"musk_reply": "reply", "musk_quote": "quote",
                        "musk_repost": "repost"}[source_type])
    subject = _compact_link_label_part(
        it.get("title_en") or it.get("title_zh") or it.get("musk_quote") or it.get("summary_en")
    )
    if subject:
        details.append(subject)
    suffix = _url_status_suffix(url)
    if suffix:
        details.append(f"post {suffix}")
    if not details and idx > 0:
        details.append(f"source {idx + 1}")
    return base + (" — " + " · ".join(details[:3]) if details else "")


def normalize_items(items: list) -> list:
    """淨化、保證每筆都有合法 category、story_id 與必要欄位（含馬斯克原話/來源型別/時間戳）。"""
    out = []
    x_oembed_cache: dict[str, dict] = {}
    for i, it in enumerate(items):
        cat = it.get("category", "other")
        if cat not in CATEGORIES:
            cat = "other"
        st = (it.get("source_type") or "").strip()
        if st not in SOURCE_TYPES:
            st = "news"
        sid = (it.get("story_id") or it.get("id") or "").strip() or _slugify_story(it, i)
        links = []
        for link_idx, link in enumerate(it.get("links", [])):
            if not isinstance(link, dict) or not link.get("url"):
                continue
            cleaned = dict(link)
            canonical_url = (cleaned.get("canonical_url") or cleaned.get("x_canonical_url") or "").strip()
            if canonical_url:
                cleaned["url"] = canonical_url
            _canonicalize_x_link(cleaned, x_oembed_cache)
            cleaned["label"] = _normalize_link_label(it, cleaned, link_idx)
            cleaned.pop("_x_author_label", None)
            links.append(cleaned)
        out.append({
            "id": sid,                 # App 用 id 當 Identifiable；= story_id 讓合併後不閃爍
            "story_id": sid,
            "category": cat,
            "source_type": st,
            "title_en": (it.get("title_en") or "").strip(),
            "title_zh": (it.get("title_zh") or "").strip(),
            "summary_en": (it.get("summary_en") or "").strip(),
            "summary_zh": (it.get("summary_zh") or "").strip(),
            "musk_quote": (it.get("musk_quote") or "").strip(),
            "musk_quote_zh": (it.get("musk_quote_zh") or "").strip(),
            "importance": int(it.get("importance") or 3),
            "first_seen": (it.get("first_seen") or "").strip(),
            "updated_at": (it.get("updated_at") or "").strip(),
            "links": links,
        })
    return out


def load_today_items(repo: Path, date_str: str) -> list:
    """讀回今日 digest 的 items_flat（供餵給 Grok 做演進式合併）。無檔/讀不到＝空清單。"""
    path = repo / "digests" / f"{date_str}.json"
    if not path.exists():
        return []
    try:
        return load_public(path).get("items_flat", []) or []
    except Exception as e:
        print(f"[merge] 讀今日既有條目失敗（當空處理）：{e}")
        return []


def _merge_one(old: dict, new: dict, run_iso: str) -> dict:
    """同一 story_id：用 Grok 新版內容，但保留 first_seen、取較高 importance、聯集 links。"""
    new["first_seen"] = old.get("first_seen") or new.get("first_seen") or run_iso
    new["importance"] = max(int(new.get("importance", 3)), int(old.get("importance", 3)))
    seen_urls = {l["url"] for l in new.get("links", [])}
    new["links"] = new.get("links", []) + [l for l in old.get("links", []) if l["url"] not in seen_urls]
    # 馬斯克原話：新版沒抓到就沿用舊的，別讓引用消失
    if not new.get("musk_quote") and old.get("musk_quote"):
        new["musk_quote"] = old["musk_quote"]; new["musk_quote_zh"] = old.get("musk_quote_zh", "")
    new["updated_at"] = run_iso
    return new


def merge_daily_items(existing_flat: list, grok_items: list, run_iso: str) -> tuple[list, int, int]:
    """以 story_id 為主鍵把 Grok 回傳的「全日列表」併進今日既有條目。
    - Grok 已做語意合併、回傳完整列表 → 這裡用 story_id 兜底（保 first_seen / links / 原話）。
    - 安全網：Grok 漏回但 importance>=3 的舊條目自動保留，避免重要資訊一天內被洗掉。"""
    old_by_id = {it.get("story_id") or it.get("id"): it for it in existing_flat}
    out, seen_ids = [], set()
    updated = 0
    for it in normalize_items(grok_items):
        sid = it["story_id"]
        if sid in old_by_id:
            it = _merge_one(old_by_id[sid], it, run_iso); updated += 1
        else:
            it["first_seen"] = it.get("first_seen") or run_iso
            it["updated_at"] = run_iso
        out.append(it); seen_ids.add(sid)
    # 安全網：只補回 Grok 漏回且「真的重大」(importance>=4) 的舊條目，避免把已被 Grok 語意合併掉的
    # 舊重複條目又塞回來（首次遷移時舊條目 story_id 與 Grok 新 id 不一致，門檻放太低會洗不掉）。
    # 補回的舊條目過 normalize_items 補上 story_id/source_type，避免欄位缺漏。
    kept = 0
    for sid, old in old_by_id.items():
        if sid not in seen_ids and int(old.get("importance", 3)) >= 4:
            out.append(normalize_items([old])[0]); kept += 1
    # 重要度高在前；同重要度時最近更新在前（穩定排序：先排 updated_at 再排 importance）
    out.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    out.sort(key=lambda x: -int(x.get("importance", 3)))
    return out, updated, kept


def write_digest(repo: Path, date_str: str, run_iso: str, grok: dict) -> Path:
    digests = repo / "digests"
    digests.mkdir(parents=True, exist_ok=True)
    path = digests / f"{date_str}.json"

    if path.exists():
        doc = load_public(path)
    else:
        doc = {"date": date_str, "runs": []}

    existing_flat = doc.get("items_flat", []) or []
    # Grok 回傳的是「全日合併後完整列表」→ 以 story_id 兜底合併、整表替換（不再 append）
    merged, updated_n, kept_n = merge_daily_items(existing_flat, grok.get("items", []), run_iso)
    doc["items_flat"] = merged

    # 依分類聚合（App 直接讀 categories）
    by_cat = {c: [] for c in CATEGORIES}
    for it in doc["items_flat"]:
        by_cat[it["category"]].append(it)
    doc["categories"] = by_cat

    doc.setdefault("runs", [])
    doc["runs"].append({
        "generated_at": run_iso,
        "backend": grok.get("_usage", {}).get("backend", DIGEST_BACKEND),
        "model": grok.get("_usage", {}).get("model", ""),
        "brief_en": grok.get("brief_en", ""),
        "brief_zh": grok.get("brief_zh", ""),
        "item_count": len(doc["items_flat"]),
        "updated_count": updated_n,
        "kept_count": kept_n,
        "usage": grok.get("_usage", {}),
    })
    # 最新一次的 brief 放頂層方便 App 顯示（Grok 已重建為全日視角）
    doc["brief_en"] = grok.get("brief_en", "")
    doc["brief_zh"] = grok.get("brief_zh", "")
    doc["updated_at"] = run_iso

    dump_public(path, doc)
    return path


def rebuild_index(repo: Path):
    digests = repo / "digests"
    files = sorted(digests.glob("*.json"), reverse=True)
    index = {"updated_at": datetime.now(timezone.utc).isoformat(), "dates": []}
    for f in files:
        try:
            doc = load_public(f)
        except Exception:
            continue
        index["dates"].append({
            "date": doc.get("date"),
            "file": f"digests/{f.name}",
            "item_count": len(doc.get("items_flat", [])),
            "brief_zh": doc.get("brief_zh", ""),
            "updated_at": doc.get("updated_at", ""),
        })
    dump_public(repo / "index.json", index)
    # latest.json = 最新一天，App 首屏直接讀它最省
    if files:
        latest = load_public(files[0])
        dump_public(repo / "latest.json", latest)


# ------------------------------------------------------------- 週報 weekly ----
# 純程式聚合（不另呼叫 Grok）：把 digests/*.json 依 ISO 週（台北日期）聚合成
# weekly/<YYYY-Www>.json + weekly_index.json，供 App「週報」分頁直讀。
# 週報內容＝該週每日 brief（day-by-day）＋ 全週去重後的重點故事 Top N ＋ 分類統計。

WEEKLY_TOP_MIN_IMPORTANCE = 4
WEEKLY_TOP_CAP = 20
WEEKLY_DAY_HEADLINES = 3    # 週報每日條列重點：取當日重要度最高的前 N 則標題


def week_key(date_str: str) -> str:
    """'2026-07-12' → ISO 週 '2026-W28'（用 isocalendar()[i] 索引寫法相容 Python 3.9）。"""
    iso = datetime.strptime(date_str, "%Y-%m-%d").isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def build_week_doc(week: str, day_docs: list, now_iso: str) -> dict:
    """把一週內的每日 digest 文件聚合成一份週報文件。"""
    day_docs = sorted(day_docs, key=lambda d: d.get("date") or "")
    days, merged = [], {}
    for doc in day_docs:
        items = doc.get("items_flat", []) or []
        # 每日條列重點（headlines）：當日重要度最高的前 N 則標題。
        # App 週報以此取代整段 brief（brief 仍保留欄位供舊版 App / 相容用）。
        day_top = sorted(items, key=lambda x: x.get("updated_at", ""), reverse=True)
        day_top.sort(key=lambda x: -int(x.get("importance", 3)))
        day_top = day_top[:WEEKLY_DAY_HEADLINES]
        days.append({
            "date": doc.get("date"),
            "brief_en": doc.get("brief_en", ""),
            "brief_zh": doc.get("brief_zh", ""),
            "headlines_zh": [it.get("title_zh") or it.get("title_en") or "" for it in day_top],
            "headlines_en": [it.get("title_en") or it.get("title_zh") or "" for it in day_top],
            "item_count": len(items),
        })
        # 跨日同故事去重：同 story_id 取（重要度, 更新時間）較高者
        for it in items:
            sid = it.get("story_id") or it.get("id") or ""
            old = merged.get(sid)
            if old is None or \
               (int(it.get("importance", 3)), it.get("updated_at", "")) >= \
               (int(old.get("importance", 3)), old.get("updated_at", "")):
                merged[sid] = it
    all_items = sorted(merged.values(), key=lambda x: x.get("updated_at", ""), reverse=True)
    all_items.sort(key=lambda x: -int(x.get("importance", 3)))
    top = [it for it in all_items if int(it.get("importance", 3)) >= WEEKLY_TOP_MIN_IMPORTANCE]
    if len(top) < 5:            # 淡週：重要度 4+ 不足 5 則就直接取前 10
        top = all_items[:10]
    top = top[:WEEKLY_TOP_CAP]
    cat_counts: dict = {}
    for it in merged.values():
        c = it.get("category", "other")
        cat_counts[c] = cat_counts.get(c, 0) + 1
    return {
        "week": week,
        "start_date": days[0]["date"] if days else "",
        "end_date": days[-1]["date"] if days else "",
        "updated_at": now_iso,
        "day_count": len(days),
        "item_count": len(merged),
        "days": days,
        "top_items": top,
        "category_counts": cat_counts,
    }


def rebuild_weekly(repo: Path):
    """全量重建 weekly/*.json 與 weekly_index.json（資料量小、每次重建最簡單可靠）。"""
    digests = repo / "digests"
    weekly_dir = repo / "weekly"
    weekly_dir.mkdir(parents=True, exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat()
    by_week: dict = {}
    for f in sorted(digests.glob("*.json")):
        try:
            doc = load_public(f)
        except Exception:
            continue
        date_str = doc.get("date") or f.stem
        try:
            wk = week_key(date_str)
        except ValueError:
            continue
        by_week.setdefault(wk, []).append(doc)
    index = {"updated_at": now_iso, "weeks": []}
    for wk in sorted(by_week.keys(), reverse=True):
        wdoc = build_week_doc(wk, by_week[wk], now_iso)
        dump_public(weekly_dir / f"{wk}.json", wdoc)
        index["weeks"].append({
            "week": wk,
            "file": f"weekly/{wk}.json",
            "start_date": wdoc["start_date"],
            "end_date": wdoc["end_date"],
            "day_count": wdoc["day_count"],
            "item_count": wdoc["item_count"],
        })
    dump_public(repo / "weekly_index.json", index)
    return index


# ------------------------------------------------------------------ git ----

def git(repo: Path, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True).stdout.strip()


def git_commit_push(repo: Path, date_str: str):
    if not (repo / ".git").exists():
        print(f"[git] {repo} 還不是 git repo，略過 commit（首次請見 README 初始化步驟）")
        return
    git(repo, "add", "-A")
    status = git(repo, "status", "--porcelain")
    if not status:
        print("[git] 無變更，略過 commit")
        return
    msg = f"chore(digest): {date_str} 自動更新"
    subprocess.run(["git", "-C", str(repo), "commit", "-m", msg],
                   capture_output=True, text=True)
    print(f"[git] committed: {msg}")
    if GIT_PUSH:
        # 先 pull --rebase，避免遠端有更新時 non-fast-forward 被拒（cron 反覆跑必備）
        subprocess.run(["git", "-C", str(repo), "pull", "--rebase", "origin", "main"],
                       capture_output=True, text=True)
        r = subprocess.run(["git", "-C", str(repo), "push", "origin", "main"],
                           capture_output=True, text=True)
        if r.returncode != 0:   # 競態：再 pull --rebase 一次重試
            subprocess.run(["git", "-C", str(repo), "pull", "--rebase", "origin", "main"],
                           capture_output=True, text=True)
            r = subprocess.run(["git", "-C", str(repo), "push", "origin", "main"],
                               capture_output=True, text=True)
        print("[git] push:", (r.stdout + r.stderr).strip()[:200])
    else:
        print("[git] GIT_PUSH!=1，已 commit 未 push（正式環境設 GIT_PUSH=1）")


# ------------------------------------------------------------------ main ---

def load_dotenv():
    """簡易載入專案根 .env（不覆蓋已存在的環境變數）。"""
    for cand in [ROOT / ".env", ROOT.parent / ".env",
                 Path("/mnt/d/claude_agent/.env")]:
        if cand.exists():
            for line in cand.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
            break


def main():
    global XAI_API_KEY
    load_dotenv()
    XAI_API_KEY = os.environ.get("XAI_API_KEY", "").strip()

    now_utc = datetime.now(timezone.utc)
    # 以 UTC+8（台北）日期切檔——避免凌晨(台北)還算到前一天(UTC)。每天台北 00:00 換新一份 digest。
    TPE = timezone(timedelta(hours=8))
    date_str = now_utc.astimezone(TPE).strftime("%Y-%m-%d")
    run_iso = now_utc.isoformat()
    print(f"=== MarsRadar digest run {run_iso} | 台北日期 {date_str} (backend={DIGEST_BACKEND}) ===")

    # 先載入今日既有條目，餵回給 Grok 做「同日同故事演進式合併」（避免早報/晚報重複）
    existing_items = load_today_items(REPO_DIR, date_str)
    print(f"[merge] 今日既有 {len(existing_items)} 條，餵回 Grok 做合併")

    grok = call_grok(date_str, run_iso, existing_items)
    print(f"[grok] {len(grok.get('items', []))} items, usage={grok.get('_usage')}")

    path = write_digest(REPO_DIR, date_str, run_iso, grok)
    rebuild_index(REPO_DIR)
    widx = rebuild_weekly(REPO_DIR)
    print(f"[write] {path}（週報 {len(widx.get('weeks', []))} 週已重建）")

    git_commit_push(REPO_DIR, date_str)
    print("=== done ===")


if __name__ == "__main__":
    main()
