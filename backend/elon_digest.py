#!/usr/bin/env python3
"""
MarsRadar — Elon / Tesla / SpaceX / X 動態聚合後端
=================================================

每 6 小時跑一次：
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
import shutil
import tempfile
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

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

# 只監控這份清單 → 控制成本（地雷二的應對）。可自由增減。
WATCH_HANDLES = [
    "elonmusk",      # Elon 本人
    "Tesla",         # 特斯拉官方
    "SpaceX",        # SpaceX 官方
    "xAI",           # xAI 官方
    "cybertruck",    # 產品線
    "Teslarati",     # 科技記者/媒體
    "SawyerMerritt", # 知名 Tesla/SpaceX 觀察家
]

CATEGORIES = ["elon_personal", "tesla", "spacex", "xai_x_platform", "other"]

# ----------------------------------------------------------- Prompt 共用 ----

SYSTEM_RULES = """You are MarsRadar's news editor. You produce a concise, accurate,
bilingual (English + Traditional Chinese / 繁體中文) digest of the latest Elon Musk,
Tesla, SpaceX, and xAI / X-platform developments.

RULES:
- Use ONLY information you actually find via live/web search and X reading. Do NOT invent.
- NEVER copy a full news article. Write your OWN 1-3 sentence summary and ALWAYS cite the
  original source URL so readers can click through (fair-use / 合理使用).
- Categorize every item into exactly one of:
  elon_personal | tesla | spacex | xai_x_platform | other
- Rate importance 1 (minor) to 5 (major).
- Provide BOTH an English and a Traditional-Chinese title and summary for every item.
- Also produce a top-level "one-minute brief" in EN and ZH (一分鐘看懂今日動態)."""

SCHEMA_BLOCK = """Return STRICT JSON with EXACTLY this shape:
{{
  "brief_en": "<=60 words one-minute brief",
  "brief_zh": "<=60字 一分鐘看懂今日動態",
  "items": [
    {{
      "category": "elon_personal|tesla|spacex|xai_x_platform|other",
      "title_en": "...",
      "title_zh": "...",
      "summary_en": "1-3 sentences, your own words",
      "summary_zh": "1-3 句，用你自己的話",
      "importance": 1,
      "links": [{{"label": "Source name", "url": "https://..."}}]
    }}
  ]
}}
Aim for 6-15 high-signal items. Skip low-value spam/replies."""

USER_TEMPLATE = """Today is {date} (UTC). Summarize the most notable developments from the
LAST 12 HOURS for: Elon Musk personally, Tesla, SpaceX, and xAI / the X platform.

Focus on these accounts and reputable news about them: {handles}.

""" + SCHEMA_BLOCK


def build_prompt(date_str: str) -> str:
    """組出單一段提示詞（CLI 用一段、API 拆 system/user 各用一半）。"""
    handles = ", ".join("@" + h for h in WATCH_HANDLES)
    return (SYSTEM_RULES + "\n\n" + USER_TEMPLATE.format(date=date_str, handles=handles)
            + "\n\nOutput STRICT JSON only — no markdown fences, no commentary before or after.")


def extract_json_object(text: str) -> dict:
    """從可能含前後雜訊（preamble、```fence）的文字中抽出第一個完整 JSON 物件。"""
    text = text.strip()
    # 去掉 ```json fence（若有）
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
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


def call_grok_cli(date_str: str) -> dict:
    """呼叫本機 Grok Build CLI（headless），讓它讀 X/web 後回傳結構化 JSON。
    不需要任何 API key，吃的是使用者的 Grok 訂閱。"""
    grok_bin = _resolve_grok_bin()
    timeout = int(os.environ.get("GROK_TIMEOUT", "600"))
    model = os.environ.get("GROK_MODEL", "").strip()
    prompt = build_prompt(date_str)

    # CLI 會載入 cwd 的 .mcp.json（含需 OAuth 的 server 會卡死）→ 用乾淨臨時目錄當 cwd。
    workdir = tempfile.mkdtemp(prefix="marsradar_grok_")
    prompt_file = Path(workdir) / "prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")

    cmd = [grok_bin, "--cwd", workdir, "--always-approve",
           "--output-format", "json", "--prompt-file", str(prompt_file)]
    if model:
        cmd += ["--model", model]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Grok CLI 逾時（{timeout}s）。可調高 GROK_TIMEOUT 重試。")
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
    }
    return data


# --------------------------------------------------------- Grok API 後端 ----

def call_grok_api(date_str: str) -> dict:
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

    handles = ", ".join("@" + h for h in WATCH_HANDLES)
    payload = {
        "model": XAI_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_RULES},
            {"role": "user", "content": USER_TEMPLATE.format(date=date_str, handles=handles)},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "search_parameters": {
            "mode": "on",
            "from_date": (datetime.now(timezone.utc) - timedelta(hours=12)).strftime("%Y-%m-%d"),
            "max_search_results": 25,
            "sources": [
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


def call_grok(date_str: str) -> dict:
    """依 DIGEST_BACKEND 選擇後端。預設 cli（吃訂閱、不需 key）。"""
    if DIGEST_BACKEND == "api":
        return call_grok_api(date_str)
    return call_grok_cli(date_str)


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

def normalize_items(items: list) -> list:
    """淨化、保證每筆都有合法 category 與必要欄位。"""
    out = []
    for i, it in enumerate(items):
        cat = it.get("category", "other")
        if cat not in CATEGORIES:
            cat = "other"
        out.append({
            "id": it.get("id") or f"item-{i}",
            "category": cat,
            "title_en": (it.get("title_en") or "").strip(),
            "title_zh": (it.get("title_zh") or "").strip(),
            "summary_en": (it.get("summary_en") or "").strip(),
            "summary_zh": (it.get("summary_zh") or "").strip(),
            "importance": int(it.get("importance") or 3),
            "links": [l for l in it.get("links", []) if l.get("url")],
        })
    return out


def write_digest(repo: Path, date_str: str, run_iso: str, grok: dict) -> Path:
    digests = repo / "digests"
    digests.mkdir(parents=True, exist_ok=True)
    path = digests / f"{date_str}.json"

    if path.exists():
        doc = load_public(path)
    else:
        doc = {"date": date_str, "runs": []}

    new_items = normalize_items(grok.get("items", []))

    # 以 (title_en 前 40 字 + category) 去重，避免同日重複跑時灌水
    seen = {(r["title_en"][:40].lower(), r["category"]) for r in doc.get("items_flat", [])}
    merged_new = [it for it in new_items
                  if (it["title_en"][:40].lower(), it["category"]) not in seen]

    doc.setdefault("items_flat", [])
    doc["items_flat"].extend(merged_new)

    # 依分類聚合（App 直接讀 categories）
    by_cat = {c: [] for c in CATEGORIES}
    for it in doc["items_flat"]:
        by_cat[it["category"]].append(it)
    doc["categories"] = by_cat

    doc["runs"].append({
        "generated_at": run_iso,
        "backend": grok.get("_usage", {}).get("backend", DIGEST_BACKEND),
        "model": grok.get("_usage", {}).get("model", ""),
        "brief_en": grok.get("brief_en", ""),
        "brief_zh": grok.get("brief_zh", ""),
        "new_item_count": len(merged_new),
        "usage": grok.get("_usage", {}),
    })
    # 最新一次的 brief 放頂層方便 App 顯示
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

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    run_iso = now.isoformat()
    print(f"=== MarsRadar digest run {run_iso} (backend={DIGEST_BACKEND}) ===")

    grok = call_grok(date_str)
    print(f"[grok] {len(grok.get('items', []))} items, usage={grok.get('_usage')}")

    path = write_digest(REPO_DIR, date_str, run_iso, grok)
    rebuild_index(REPO_DIR)
    print(f"[write] {path}")

    git_commit_push(REPO_DIR, date_str)
    print("=== done ===")


if __name__ == "__main__":
    main()
