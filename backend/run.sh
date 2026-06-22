#!/usr/bin/env bash
# MarsRadar 後端 runner —— 給 cron 用。每 2 小時跑一次。
#
# 預設後端＝Grok Build CLI（DIGEST_BACKEND=cli）：吃你的 Grok 訂閱、不需要任何 API key，
# 只需要這台機器上已安裝並登入 grok CLI（curl -fsSL https://x.ai/cli/install.sh | bash）。
# 純標準函式庫，不需 venv / pip。
#
# 若要改用 xAI REST API：在環境設 DIGEST_BACKEND=api 並提供 XAI_API_KEY（需 pip install requests）。
set -euo pipefail
cd "$(dirname "$0")"

# 後端：webgrok（預設，驅動瀏覽器 grok.com 讀 X）/ cli（Grok CLI，讀 X 快路徑已壞、會逾時）/ api
export DIGEST_BACKEND="${DIGEST_BACKEND:-webgrok}"
# 正式環境設 1 才真的 push 到公開 repo
export GIT_PUSH="${GIT_PUSH:-1}"
# Grok CLI 合併全日資料時偶爾會超過 10 分鐘；cron 寧可等久一點，不要錯過更新。
export GROK_TIMEOUT="${GROK_TIMEOUT:-1800}"
# webgrok 等 grok.com 讀 X 生成完成的逾時秒數
export WEBGROK_GEN_TIMEOUT="${WEBGROK_GEN_TIMEOUT:-240}"

# 有 venv 就用（api 後端需要 requests）；沒有就用系統 python3（cli/webgrok 只需標準庫）
PY="$(dirname "$0")/.venv/bin/python3"
[ -x "$PY" ] || PY="python3"

# 依後端挑腳本：webgrok→瀏覽器版；其餘→原 CLI/API 版（同一支 elon_digest.py）
SCRIPT=elon_digest.py
[ "$DIGEST_BACKEND" = "webgrok" ] && SCRIPT=elon_digest_webgrok.py

exec "$PY" "$SCRIPT" >> "$(dirname "$0")/digest.log" 2>&1
