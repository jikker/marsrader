#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

"""
MarsRadar —— Elon digest「讀 X」後端（web 版 Grok 路線）
========================================================

為什麼有這支：
  原本 `elon_digest.py` 用 **Grok Build CLI** 讀 X。CLI 讀 X 的快路徑全壞了
  （Chrome cookie 鎖、bird 拿不到 cookie），只能退到慢 nitter → cron 一直逾時。
  實測「瀏覽器上已登入的 web 版 grok（grok.com）」有原生 X 存取，約 90 秒就能
  讀回結構化的馬斯克推文 JSON。這支就是把「讀 X」改成驅動 grok.com 分頁。

它做什麼：
  1. 透過常駐的 browser MCP daemon（127.0.0.1，SSE）找/開一個 grok.com 分頁
  2. 把一段「請用你的 X 存取列出 Elon 過去 12 小時所有貼文（JSON 陣列）」的 prompt
     打進 grok 的 contenteditable 輸入框（TipTap/ProseMirror）、點提交、等它讀 X 生成完
  3. 從回應的 code block 取出 JSON 陣列（type/text/engaged_with/link/time_utc/topic/importance）
  4. 把每個 item 對映成 elon_digest.py 的 digest schema
  5. **重用 elon_digest.py 既有的 merge / 寫檔（digests/<date>.json + latest.json +
     index.json）/ git commit+push 函式**，把今天的 digest 合併寫出、可選 push

跟 elon_digest.py 的關係：
  - schema、normalize_items、merge_daily_items、write_digest、rebuild_index、
    git_commit_push、dump_public/load_public、load_dotenv 全部直接 import 重用，
    這支只負責「換掉讀 X 的來源」（CLI → 瀏覽器 grok.com），寫檔/合併/push 完全一致。

前置條件（執行時，非本檔負責）：
  - browser MCP daemon 已在 127.0.0.1:3457（SSE）跑著（與 imagegen 工具同一支 daemon）
  - 瀏覽器（含 Claude Browser Agent 擴充功能）已開、且已登入 grok.com（有 X 原生存取）

環境變數：
  MARSRADAR_MCP_BASE   選填，browser MCP daemon base URL（預設 http://127.0.0.1:3457）
  WEBGROK_LOOKBACK_HOURS 選填，要 grok 回顧幾小時（預設 12）
  WEBGROK_GEN_TIMEOUT  選填，等 grok 讀 X 生成完成的逾時秒數（預設 240）
  WEBGROK_REUSE_TAB    選填，"1"（預設）找現有 grok.com 分頁複用；"0" 一律開新分頁
  DIGEST_REPO_DIR      選填，資料倉庫本機路徑（同 elon_digest.py）
  GIT_PUSH             選填，"1" 才真的 push（預設 0，同 elon_digest.py）
  MARSRADAR_ENC_KEY    選填，設了就把公開 JSON 加密寫出（同 elon_digest.py）

用法：
  python3 elon_digest_webgrok.py
  GIT_PUSH=1 python3 elon_digest_webgrok.py
"""

import os
import re
import sys
import json
import time
import threading
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 重用 elon_digest 既有的 schema/合併/寫檔/index/git/env 全套（不重造輪子）
import elon_digest as ed


# ----------------------------------------------------------------- 設定 ----

MCP_BASE = os.environ.get("MARSRADAR_MCP_BASE", "http://127.0.0.1:3457").rstrip("/")
LOOKBACK_HOURS = ed.env_int("WEBGROK_LOOKBACK_HOURS", 12)
GEN_TIMEOUT = ed.env_int("WEBGROK_GEN_TIMEOUT", 240)
REUSE_TAB = os.environ.get("WEBGROK_REUSE_TAB", "1") != "0"

GROK_URL = "https://grok.com/"

# grok.com DOM（見 grok-chat skill）：輸入框是 TipTap/ProseMirror contenteditable，
# 按 Enter 不送出 → 必須點提交鈕。提交鈕 aria-label='提交'。
EDITOR_SEL = ".tiptap.ProseMirror"
# data-testid 比 aria-label 穩（grok.com UI 會在地化：提交/Submit…）。送出鈕只在輸入框有字時才出現。
SUBMIT_SEL = "button[data-testid='chat-submit']"


# --------------------------------------------------------- 最小 MCP client ----

class MCP:
    """最小 MCP-over-SSE client（純標準庫），與 imagegen 的 mcp_client.py 同一條鏈路。
    GET /sse 開 stream（背景 thread 讀）→ endpoint event 拿 /messages?sessionId=...
    → initialize → notifications/initialized → tools/call，回應以 JSON-RPC id 配對。"""

    def __init__(self, base=MCP_BASE):
        self.base = base
        self.endpoint = None
        self.responses = {}
        self.ev = {}
        self.ready = threading.Event()
        self._id = 1
        self._lock = threading.Lock()
        self._sse_err = None
        threading.Thread(target=self._sse, daemon=True).start()

    def _sse(self):
        try:
            resp = urllib.request.urlopen(urllib.request.Request(
                self.base + "/sse", headers={"Accept": "text/event-stream"}), timeout=900)
        except Exception as e:
            self._sse_err = e
            self.ready.set()   # 解除 connect() 的等待，讓它報錯
            return
        event, data = None, []
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line == "":
                if event == "endpoint" and data:
                    p = data[0]
                    self.endpoint = self.base + p if p.startswith("/") else p
                    self.ready.set()
                elif data:
                    try:
                        m = json.loads(data[0])
                        mid = m.get("id")
                        if mid is not None:
                            self.responses[mid] = m
                            if mid in self.ev:
                                self.ev[mid].set()
                    except Exception:
                        pass
                event, data = None, []
                continue
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())

    def _post(self, payload):
        body = json.dumps(payload).encode()
        urllib.request.urlopen(urllib.request.Request(
            self.endpoint, data=body,
            headers={"Content-Type": "application/json"}, method="POST"), timeout=30).read()

    def _rpc(self, method, params, timeout=120):
        with self._lock:
            mid = self._id
            self._id += 1
        ev = threading.Event()
        self.ev[mid] = ev
        self._post({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        if not ev.wait(timeout):
            raise TimeoutError(f"{method} timed out")
        msg = self.responses.pop(mid, {})
        if "error" in msg:
            raise RuntimeError(msg["error"])
        return msg.get("result")

    def connect(self):
        if not self.ready.wait(10):
            raise RuntimeError("沒收到 SSE endpoint（browser daemon 沒跑?）")
        if self._sse_err is not None:
            raise RuntimeError(
                f"連不上 browser MCP daemon（{self.base}/sse）：{self._sse_err!r}。"
                "請確認 daemon 已啟動、且瀏覽器擴充功能已連上。")
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "marsradar-webgrok", "version": "1.0"}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def call(self, name, arguments, timeout=120):
        res = self._rpc("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        if isinstance(res, dict) and "content" in res:
            txt = "\n".join(c.get("text", "") for c in res["content"] if c.get("type") == "text")
            return txt, res.get("isError", False)
        return json.dumps(res), False


def _unwrap(s: str):
    """解開 daemon 把 eval 結果包成 {"result": ...} 的外層，逐層回到內層值。"""
    try:
        v = json.loads(s)
        while isinstance(v, dict) and "result" in v:
            v = v["result"]
        return v
    except Exception:
        return s


# ------------------------------------------------------- grok.com 驅動 ----

class GrokTab:
    """驅動一個 grok.com 分頁：找/開分頁 → 等編輯器 → 填 prompt → 提交 → 等回覆。"""

    def __init__(self, mcp: MCP):
        self.mcp = mcp
        self.tab_id = None

    # ---- 分頁管理 ----

    def _find_existing(self):
        out, _ = self.mcp.call("browser_list_tabs", {}, timeout=30)
        try:
            data = json.loads(out)
        except Exception:
            return None
        tabs = data.get("tabs", data) if isinstance(data, dict) else data
        if not isinstance(tabs, list):
            return None
        for t in tabs:
            url = (t.get("url") or "") if isinstance(t, dict) else ""
            if "grok.com" in url:
                return t.get("tabId") or t.get("id")
        return None

    def open(self):
        if REUSE_TAB:
            tid = self._find_existing()
            if tid is not None:
                self.tab_id = tid
                # 確保切到該分頁（避免操作到背景分頁時 CDP 焦點問題）
                try:
                    self.mcp.call("browser_switch_tab", {"tabId": self.tab_id}, timeout=30)
                except Exception:
                    pass
                # 導回 grok.com/ 開「全新對話」→ 清掉舊對話的 code block（否則 grab 會抓到舊 JSON）
                # 也確保輸入框是空的、送出鈕狀態乾淨。
                try:
                    self.mcp.call("browser_navigate", {"url": GROK_URL, "tabId": self.tab_id}, timeout=60)
                except Exception:
                    pass
                time.sleep(2)
                print(f"[webgrok] 複用既有 grok.com 分頁 tabId={self.tab_id}（已導回新對話）")
                return self.tab_id
        out, _ = self.mcp.call("browser_create_tab", {"url": GROK_URL}, timeout=60)
        try:
            self.tab_id = json.loads(out).get("tabId")
        except Exception:
            m = re.search(r'"tabId"\s*:\s*(\d+)', out)
            self.tab_id = int(m.group(1)) if m else None
        if self.tab_id is None:
            raise RuntimeError(f"無法取得 grok.com 分頁 tabId：{out[:160]}")
        print(f"[webgrok] 開新 grok.com 分頁 tabId={self.tab_id}")
        return self.tab_id

    # ---- 低階 eval ----

    def _eval(self, code, timeout=30):
        # 用 _cdp 版：它能跑多語句 IIFE（grab/editor_text 都是多語句）；非 cdp 版遇到 `;` 會
        # 報 "Unexpected token ';'"。cdp 偶爾回 null → 靠呼叫端容錯（送出成功判定改用 URL，不靠 eval）。
        out, _ = self.mcp.call("browser_eval_js_cdp",
                               {"code": code, "tabId": self.tab_id}, timeout=timeout)
        return out

    def _page_url(self):
        """用 browser_get_page_info 拿目前 URL（不經 eval，最可靠）。送出成功→URL 變 grok.com/c/<id>。"""
        out, _ = self.mcp.call("browser_get_page_info", {"tabId": self.tab_id}, timeout=30)
        try:
            data = json.loads(out)
            return data.get("url", "") if isinstance(data, dict) else ""
        except Exception:
            return ""

    def wait_editor(self, timeout=40):
        deadline = time.time() + timeout
        while time.time() < deadline:
            out = self._eval(f"(() => !!document.querySelector('{EDITOR_SEL}'))()")
            if "true" in str(_unwrap(out)).lower():
                return True
            time.sleep(2)
        return False

    def _editor_text(self):
        out = self._eval(
            f"(() => {{const e=document.querySelector('{EDITOR_SEL}');"
            "return e?(e.innerText||'').trim():'';}})()")
        v = _unwrap(out)
        return v if isinstance(v, str) else str(v)

    def _editor_empty(self):
        return self._editor_text() == ""

    # ---- 送出 ----

    def send(self, prompt: str) -> bool:
        """填入 prompt 後「點提交鈕」送出。
        grok 的輸入框是 TipTap/ProseMirror contenteditable：
          - 必須用 browser_fill clear=false 逐字輸入才會觸發 React onChange
            （JS 注入 / execCommand / paste 都不會同步 state → 送出空字串）
          - 按 Enter 不送出 → 必須點提交鈕 aria-label='提交'
        """
        # 先等任何前一次生成結束（否則送出鈕其實是「停止」鈕，按下去是停止不是送出）
        for _ in range(30):
            if not self._generating():
                break
            time.sleep(1)
        # 聚焦
        self.mcp.call("browser_click",
                      {"selector": EDITOR_SEL, "tabId": self.tab_id, "humanLike": True},
                      timeout=30)
        # 逐字輸入（clear=false 是 TipTap 鐵律）
        self.mcp.call("browser_fill",
                      {"selector": EDITOR_SEL, "value": prompt, "clear": False, "tabId": self.tab_id},
                      timeout=120)
        # 等「輸入框真的有字」（= fill 生效、送出鈕才會 enabled）。用 EDITOR_SEL（無引號衝突）判定。
        deadline = time.time() + 10
        while time.time() < deadline:
            if not self._editor_empty():
                break
            time.sleep(0.5)
        time.sleep(0.6)
        # 送出成功判定＝URL 從 grok.com/ 變成 grok.com/c/<對話id>（最可靠，不靠 flaky 的 eval）。
        # 用「平實 click」不要 humanLike（humanLike 會 hover+偏移、常落在鈕內 SVG 上點不到）。
        url_before = self._page_url()
        for _ in range(3):
            self.mcp.call("browser_click",
                          {"selector": SUBMIT_SEL, "tabId": self.tab_id, "wait_after": 1500},
                          timeout=30)
            # 點完等幾秒看 URL 有沒有變成對話頁
            for _ in range(6):
                time.sleep(1.0)
                u = self._page_url()
                if "/c/" in u and u != url_before:
                    return True
            # 沒成功：可能 fill 被清掉了，重填再試
            if self._editor_empty():
                self.mcp.call("browser_fill",
                              {"selector": EDITOR_SEL, "value": prompt, "clear": False, "tabId": self.tab_id},
                              timeout=120)
                time.sleep(0.8)
        return "/c/" in self._page_url()

    # ---- 取回覆 ----

    def _generating(self):
        """grok 生成中時送出鈕會變「停止」鈕。實測 aria-label 是「停止模型響應」(非「停止」)，
        所以用 substring 比對(停止 / stop)，別用精確 aria-label，否則永遠偵測不到生成中→
        會在生成中誤點到停止鈕(=同一顆鈕)，把上一輪生成停掉、新 prompt 也送不出去。"""
        out = self._eval(
            "(() => Array.from(document.querySelectorAll('button[aria-label]'))"
            ".some(b => /停止|stop/i.test(b.getAttribute('aria-label')||'')))()")
        return "true" in str(_unwrap(out)).lower()

    # 抓最後一則 grok 回覆內所有 code block 的文字。grok.com 上 CDP eval 回傳偶爾
    # 不可靠（grok-chat skill 有記）→ 把結果塞進 document.title 當側通道，再用
    # browser_get_page_info 讀 title 取回（雙保險：先試 eval 直接回傳，失敗才走 title）。
    _GRAB_JS = r"""
(() => {
  const blocks = Array.from(document.querySelectorAll('pre code, pre'))
    .map(el => (el.innerText || '').trim())
    .filter(t => t.length > 0);
  // 只要含 '[' 與 ']' 的，優先（JSON 陣列）
  let best = '';
  for (const b of blocks) {
    if (b.indexOf('[') !== -1 && b.indexOf(']') !== -1 && b.length > best.length) best = b;
  }
  if (!best && blocks.length) best = blocks[blocks.length - 1];
  return best;
})()
"""

    def _page_title(self):
        out, _ = self.mcp.call("browser_get_page_info", {"tabId": self.tab_id}, timeout=30)
        try:
            data = json.loads(out)
            return data.get("title", "") if isinstance(data, dict) else ""
        except Exception:
            return ""

    def grab_codeblock(self) -> str:
        """抓最後一則回覆裡最像 JSON 陣列的 code block 文字。"""
        # 先試 eval 直接回傳
        raw = _unwrap(self._eval(self._GRAB_JS, timeout=30))
        if isinstance(raw, str) and raw.strip():
            return raw
        # 退路：把結果塞 title 再讀（grok.com eval 回傳偶爾為 null）
        sentinel = "__MR_GRAB__:"
        self._eval(
            "(() => { try { const r = (" + self._GRAB_JS + "); "
            "document.title = '" + sentinel + "' + encodeURIComponent(r || ''); } "
            "catch(e){ document.title = '" + sentinel + "ERR'; } return true; })()",
            timeout=30)
        time.sleep(0.5)
        title = self._page_title()
        if sentinel in title:
            enc = title.split(sentinel, 1)[1]
            try:
                return urllib.parse.unquote(enc)
            except Exception:
                return enc
        return ""

    def wait_reply(self, timeout=GEN_TIMEOUT) -> str:
        """等 grok 讀 X + 生成完成。完成判定＝有 code block 內容 + 不在生成中 + 連續兩次穩定。
        回傳該 code block 文字（抓不到回 ''）。"""
        deadline = time.time() + timeout
        last, stable = "", 0
        # 給 grok 一點啟動時間（它要先去讀 X）
        time.sleep(5)
        while time.time() < deadline:
            gen = self._generating()
            block = self.grab_codeblock() if not gen else ""
            if block and block == last and not gen:
                stable += 1
                if stable >= 2:
                    return block
            else:
                stable = 0
            if block:
                last = block
            time.sleep(4)
        # 逾時前最後再抓一次（可能已生成完只是沒湊滿穩定次數）
        return self.grab_codeblock() or last


# ------------------------------------------------------------ prompt ----

# 用「原本 CLI 版的完整複雜提示詞」(elon_digest.SYSTEM_RULES + USER_TEMPLATE + SCHEMA_BLOCK)，
# 讓 grok 直接吐含 標題/中英摘要/馬斯克原話+中譯/分類/重要度/links + 整日 brief 的完整 digest，
# 並與既有條目演進式合併。送出穩定問題已用「URL 變化判定」解決，與 prompt 長度無關，故可用長 prompt。
# 唯一差別：要 grok 把 JSON 放進一個 ```json fenced code block(方便穩定 grab)。
def build_webgrok_prompt(run_iso: str, date_str: str, existing_items: list) -> str:
    existing_json = (json.dumps(ed._compact_existing(existing_items, limit=25),
                                ensure_ascii=False, indent=1)
                     if existing_items else "[]")
    user = ed.USER_TEMPLATE.format(date=date_str, run_iso=run_iso or date_str,
                                   lookback_hours=LOOKBACK_HOURS,
                                   existing_items_json=existing_json)
    return (ed.SYSTEM_RULES + "\n\n" + user
            + "\n\nIMPORTANT OUTPUT FORMAT: output the STRICT JSON object inside ONE ```json fenced "
              "code block, nothing before or after the code block.")


def parse_grok_array(text: str) -> list:
    """從 grok 回的 code block 文字抽出 JSON 陣列。容忍 ```json fence、前後雜訊。"""
    if not text:
        return []
    t = text.strip()
    # 去掉 ```json fence
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        end = t.rfind("```")
        if end != -1:
            t = t[:end]
        t = t.strip()
    # 直接試
    try:
        v = json.loads(t)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            # 萬一 grok 包成 {"posts":[...]} / {"items":[...]}
            for k in ("posts", "items", "data", "results"):
                if isinstance(v.get(k), list):
                    return v[k]
    except Exception:
        pass
    # 括號計數抓第一個完整 JSON 陣列
    start = t.find("[")
    if start == -1:
        return []
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(t)):
        ch = t[i]
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
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                try:
                    arr = json.loads(t[start:i + 1])
                    return arr if isinstance(arr, list) else []
                except Exception:
                    return []
    return []


# ------------------------------------------------ grok item → digest schema ----

# grok 回的 type → digest 的 source_type（elon 本人發文一律 musk_*）
_TYPE_TO_SOURCE = {
    "post": "musk_post",
    "reply": "musk_reply",
    "quote": "musk_quote",
    "repost": "musk_quote",   # 轉推＝引用他人，歸 musk_quote（仍是「他發的」）
}

# topic 關鍵字 → digest category（粗略規則；中文標題等可後補）
def _topic_to_category(topic: str, text: str) -> str:
    blob = f"{topic} {text}".lower()
    if any(k in blob for k in ("tesla", "fsd", "cybertruck", "model ", "optimus", "robotaxi")):
        return "tesla"
    if any(k in blob for k in ("spacex", "starship", "falcon", "starlink", "raptor", "launch")):
        return "spacex"
    if any(k in blob for k in ("xai", "grok", " x ", "twitter", "x platform", "x.com")):
        return "xai_x_platform"
    # 預設：馬斯克本人發言歸 elon_personal
    return "elon_personal"


def _make_story_id(it: dict, idx: int, date_str: str) -> str:
    """從 link 的 status id 或 topic 生一個穩定 story_id。"""
    link = (it.get("link") or "").strip()
    m = re.search(r"/status/(\d+)", link)
    if m:
        return f"musk-{m.group(1)}"
    topic = (it.get("topic") or it.get("text") or "").lower()
    slug = "-".join(p for p in re.sub(r"[^a-z0-9]+", "-", topic).split("-") if p)[:48]
    return f"musk-{slug or 'post'}-{date_str}-{idx}"


def grok_items_to_digest(grok_arr: list, run_iso: str, date_str: str) -> dict:
    """把 web-grok 回的 X 貼文陣列對映成 elon_digest 的 grok dict（含 items + brief）。
    回傳形如 call_grok() 的結構：{"brief_en","brief_zh","items":[...],"_usage":{...}}。
    中文欄位（title_zh/summary_zh/musk_quote_zh/brief_zh）先用簡單規則或留空，可後補翻譯。"""
    items = []
    topics = []
    for idx, it in enumerate(grok_arr):
        if not isinstance(it, dict):
            continue
        text = (it.get("text") or "").strip()
        if not text:
            continue
        typ = (it.get("type") or "post").strip().lower()
        source_type = _TYPE_TO_SOURCE.get(typ, "musk_post")
        topic = (it.get("topic") or "").strip()
        engaged = (it.get("engaged_with") or "").strip()
        link = (it.get("link") or "").strip()
        time_utc = (it.get("time_utc") or "").strip() or run_iso
        try:
            importance = int(it.get("importance") or 3)
        except (TypeError, ValueError):
            importance = 3
        importance = max(1, min(5, importance))

        category = _topic_to_category(topic, text)

        # title：用 topic 當骨幹（沒有就截一段 text）
        title_en = topic or (text[:60] + ("…" if len(text) > 60 else ""))
        if engaged and typ in ("reply", "quote", "repost"):
            title_en = f"{title_en} ({typ} → {engaged})".strip()

        # summary：用自己的話濃縮（這裡先用 text + 互動對象組句；可後補更精煉的 LLM 摘要）
        summary_en = text if len(text) <= 280 else text[:277] + "…"
        if engaged and typ != "post":
            summary_en = f"[{typ} to {engaged}] {summary_en}"

        links = []
        if link:
            links.append({"label": "Elon on X", "url": link})

        items.append({
            "story_id": _make_story_id(it, idx, date_str),
            "category": category,
            "source_type": source_type,
            "title_en": title_en,
            "title_zh": "",            # 後補：可接翻譯
            "summary_en": summary_en,
            "summary_zh": "",          # 後補：可接翻譯
            "musk_quote": text[:280],  # 馬斯克原話＝貼文本文
            "musk_quote_zh": "",       # 後補：可接翻譯
            "importance": importance,
            "first_seen": time_utc,
            "updated_at": run_iso,
            "links": links,
        })
        if topic:
            topics.append(topic)

    # brief：用最高重要度幾則的 topic 組一句英文摘要；中文留空（可後補）
    if items:
        top = sorted(items, key=lambda x: -x["importance"])[:5]
        top_topics = [t["title_en"] for t in top]
        brief_en = ("Elon Musk on X in the last %dh: " % LOOKBACK_HOURS) + "; ".join(top_topics)
        brief_en = brief_en[:300]
    else:
        brief_en = "No new @elonmusk X activity captured in the window."

    return {
        "brief_en": brief_en,
        "brief_zh": "",   # 後補：可接翻譯（elon_digest 寫檔時容許空字串）
        "items": items,
        "_usage": {
            "backend": "grok-web",
            "model": "grok.com (browser)",
            "lookback_hours": LOOKBACK_HOURS,
            "raw_count": len(grok_arr),
            "mapped_count": len(items),
        },
    }


# --------------------------------------------------------------- 讀 X ----

def read_x_via_webgrok(run_iso: str, date_str: str, existing_items: list) -> dict:
    """完整流程：連 daemon → 開/找 grok.com 分頁 → 送「完整複雜 prompt」→ 等回覆 →
    用 elon_digest.extract_json_object 解析成完整 digest dict(brief+items 已合併)。"""
    mcp = MCP()
    mcp.connect()
    print(f"[webgrok] 已連上 browser MCP daemon（{MCP_BASE}）")

    tab = GrokTab(mcp)
    tab.open()
    if not tab.wait_editor(45):
        raise RuntimeError("grok.com 編輯器（.tiptap.ProseMirror）未就緒——"
                           "確認瀏覽器已開 grok.com 且已登入。")

    prompt = build_webgrok_prompt(run_iso, date_str, existing_items)
    print(f"[webgrok] 送出完整 prompt（{len(prompt)} 字，lookback={LOOKBACK_HOURS}h，"
          f"既有 {len(existing_items)} 條）→ grok 讀 X+合併中…")
    if not tab.send(prompt):
        raise RuntimeError("prompt 送出失敗（送出後 URL 沒變成對話頁）。")

    block = tab.wait_reply(GEN_TIMEOUT)
    if not block:
        raise RuntimeError(f"等不到 grok 回覆（逾時 {GEN_TIMEOUT}s）。")
    print(f"[webgrok] 取得回覆（{len(block)} 字）")

    # 用 elon_digest 既有的健壯解析(容忍 ```fence、前後雜訊)抽出完整 digest 物件。
    try:
        data = ed.extract_json_object(block)
    except Exception as e:
        print(f"[webgrok] ⚠ JSON 解析失敗：{e!r}；回覆前 300 字：{block[:300]!r}", file=sys.stderr)
        raise
    items = data.get("items", [])
    print(f"[webgrok] 解析出完整 digest：{len(items)} items，brief_en={bool(data.get('brief_en'))}")
    data["_usage"] = {
        "backend": "grok-web",
        "model": "grok.com (browser)",
        "lookback_hours": LOOKBACK_HOURS,
        "item_count": len(items),
    }
    return data


# ------------------------------------------------------------------ main ---

def main():
    # 重用 elon_digest 的 .env 載入與 UTC+8 切檔邏輯
    ed.load_dotenv()

    now_utc = datetime.now(timezone.utc)
    TPE = timezone(timedelta(hours=8))
    date_str = now_utc.astimezone(TPE).strftime("%Y-%m-%d")
    run_iso = now_utc.isoformat()
    repo = ed.REPO_DIR
    print(f"=== MarsRadar webgrok run {run_iso} | 台北日期 {date_str} "
          f"| repo={repo} | push={ed.GIT_PUSH} ===")

    # 0) 先載入今日既有條目，連同完整 prompt 一起餵 grok 做演進式合併(產出完整 digest)
    existing = ed.load_today_items(repo, date_str)
    print(f"[merge] 今日既有 {len(existing)} 條（一併餵給 grok 合併，write_digest 內再以 story_id 收斂）")

    # 1) 用 web 版 grok 讀 X（完整複雜 prompt → 完整 digest）
    try:
        grok = read_x_via_webgrok(run_iso, date_str, existing)
    except Exception as e:
        print(f"[webgrok] ❌ 讀 X 失敗：{e!r}", file=sys.stderr)
        sys.exit(2)

    print(f"[webgrok] {len(grok.get('items', []))} items, usage={grok.get('_usage')}")

    # 2) 重用 elon_digest 既有的 merge / 寫檔 / index / git
    try:
        path = ed.write_digest(repo, date_str, run_iso, grok)
        ed.rebuild_index(repo)
        print(f"[write] {path}")
        ed.git_commit_push(repo, date_str)
    except Exception as e:
        print(f"[webgrok] ❌ 寫檔/合併/push 失敗：{e!r}", file=sys.stderr)
        sys.exit(3)

    print("=== done ===")


if __name__ == "__main__":
    main()
