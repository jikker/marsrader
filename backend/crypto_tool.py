#!/usr/bin/env python3
"""
MarsRadar 公開 JSON 加密小工具
==============================

用途：切換到加密時，把倉庫裡「現有的明文 JSON」一次性轉成加密信封（或反向解回明文驗證）。
平常的 2 小時排程由 elon_digest.py 自己加密，這支只在「手動切換 / 驗證」時用。

金鑰：環境變數 MARSRADAR_ENC_KEY（64 hex 字元＝32 bytes），與 App Config.encKeyHex 相同。

用法：
  # 把倉庫根的 index.json / latest.json / digests/*.json 全部就地加密
  MARSRADAR_ENC_KEY=<hex> python3 crypto_tool.py encrypt [REPO_DIR]

  # 反向：把加密信封解回明文（驗證 / 緊急回退）
  MARSRADAR_ENC_KEY=<hex> python3 crypto_tool.py decrypt [REPO_DIR]

  # 只驗證 App 能否解（不改檔），逐檔印出解密後的 byte 數
  MARSRADAR_ENC_KEY=<hex> python3 crypto_tool.py verify [REPO_DIR]

REPO_DIR 預設＝本檔上一層（與 elon_digest.py 的 DEFAULT_REPO 一致）。
⚠️ 切到加密前，務必先讓「內建解密的新 App build」上架，否則現役 App 會讀不到。
"""
import os
import sys
import json
import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_REPO = ROOT.parent.resolve()
ENC_TAG = "marsradar_enc"


def key() -> bytes:
    h = os.environ.get("MARSRADAR_ENC_KEY", "").strip()
    if not h:
        sys.exit("缺少 MARSRADAR_ENC_KEY（64 hex 字元）。")
    k = bytes.fromhex(h)
    if len(k) != 32:
        sys.exit(f"金鑰需 32 bytes，目前 {len(k)}。")
    return k


def is_envelope(obj) -> bool:
    return isinstance(obj, dict) and bool(obj.get(ENC_TAG)) and bool(obj.get("blob"))


def encrypt_text(text: str, k: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    ct = AESGCM(k).encrypt(nonce, text.encode("utf-8"), None)
    blob = base64.b64encode(nonce + ct).decode("ascii")
    return json.dumps({ENC_TAG: 1, "alg": "AES-256-GCM", "blob": blob}, ensure_ascii=False)


def decrypt_text(env_text: str, k: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    env = json.loads(env_text)
    raw = base64.b64decode(env["blob"])
    return AESGCM(k).decrypt(raw[:12], raw[12:], None).decode("utf-8")


def target_files(repo: Path):
    for name in ("index.json", "latest.json"):
        p = repo / name
        if p.exists():
            yield p
    d = repo / "digests"
    if d.exists():
        yield from sorted(d.glob("*.json"))


def atomic_write(p: Path, text: str):
    """原子寫入：先寫 .tmp 再 os.replace，避免中斷時留半寫壞檔（App 會讀不到那天）。"""
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("encrypt", "decrypt", "verify"):
        sys.exit(__doc__)
    mode = sys.argv[1]
    repo = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else DEFAULT_REPO
    k = key()
    n = 0
    for p in target_files(repo):
        # 逐檔 try：一個壞檔（BOM/非UTF8/壞JSON/錯金鑰）不應中斷整批、留下半加密的不一致倉庫。
        try:
            text = p.read_text(encoding="utf-8-sig")   # utf-8-sig：相容 Windows 編輯留下的 BOM
            obj = json.loads(text)
            if mode == "encrypt":
                if is_envelope(obj):
                    print(f"  skip (已加密) {p.name}")
                    continue
                atomic_write(p, encrypt_text(text, k))
                print(f"  encrypted {p.name}")
                n += 1
            elif mode == "decrypt":
                if not is_envelope(obj):
                    print(f"  skip (已明文) {p.name}")
                    continue
                plain = decrypt_text(text, k)
                atomic_write(p, json.dumps(json.loads(plain), ensure_ascii=False, indent=2))
                print(f"  decrypted {p.name}")
                n += 1
            else:  # verify
                if not is_envelope(obj):
                    print(f"  {p.name}: 明文（{len(text)} bytes）")
                    continue
                plain = decrypt_text(text, k)
                print(f"  {p.name}: 加密 OK → 解出 {len(plain)} bytes JSON")
                n += 1
        except Exception as e:
            print(f"  ⚠️ 跳過 {p.name}：{type(e).__name__}: {e}")
            continue
    print(f"== {mode} 完成，處理 {n} 檔（repo={repo}）==")


if __name__ == "__main__":
    main()
