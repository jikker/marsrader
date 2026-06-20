#!/usr/bin/env bash
# MarsRadar 後端 runner —— 給 cron 用。每 6 小時跑一次。
#
# 預設後端＝Grok Build CLI（DIGEST_BACKEND=cli）：吃你的 Grok 訂閱、不需要任何 API key，
# 只需要這台機器上已安裝並登入 grok CLI（curl -fsSL https://x.ai/cli/install.sh | bash）。
# 純標準函式庫，不需 venv / pip。
#
# 若要改用 xAI REST API：在環境設 DIGEST_BACKEND=api 並提供 XAI_API_KEY（需 pip install requests）。
set -euo pipefail
cd "$(dirname "$0")"

# 後端：cli（預設）/ api
export DIGEST_BACKEND="${DIGEST_BACKEND:-cli}"
# 正式環境設 1 才真的 push 到公開 repo
export GIT_PUSH="${GIT_PUSH:-1}"

# 有 venv 就用（api 後端需要 requests）；沒有就用系統 python3（cli 後端只需標準庫）
PY="$(dirname "$0")/.venv/bin/python3"
[ -x "$PY" ] || PY="python3"

exec "$PY" elon_digest.py >> "$(dirname "$0")/digest.log" 2>&1
