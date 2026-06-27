"""
update_dashboard.py — 真·一鍵:更新所有資料 → 啟動本機伺服器 → 開好可用的儀表板
========================================================================
為什麼要伺服器:儀表板用 fetch() 讀 json,瀏覽器在 file:// 下會擋(=「資料載入失敗」)。
所以必須用 web_server.py 以 http://localhost:8000 開啟,不能直接雙擊 html。

本腳本一次做完:
  1. 跑生成器:core_status(兩策略) / generate_signals(個股) / generate_evidence(回測實證) / strategy_menu(選單)
  2. 若 8000 埠沒在跑 → 背景啟動 web_server.py(會開一個小黑窗,別關它=伺服器)
  3. 用瀏覽器開 http://localhost:8000/strategy_dashboard.html

用法:
  python update_dashboard.py            # 全更新 + 開啟
  python update_dashboard.py --no-open  # 只更新不開瀏覽器
  python update_dashboard.py --fast     # 跳過個股(只更新指數兩策略,最快)
或直接雙擊 更新儀表板.bat
"""

import os
import sys
import time
import socket
import subprocess
import webbrowser

# ★ 一律把 stdout 轉 utf-8(Windows 預設 cp950 會讓 emoji/中文 print 崩潰),不論從哪個進入點呼叫
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8000
URL = f'http://localhost:{PORT}/strategy_dashboard.html'

# 生成器(依序跑)。(檔名, 說明, 是否每天必跑)
GENERATORS = [
    ('core_status.py',      '美股兩策略 A抱SMH / B輪動最強(+最低30日鎖)', True),
    ('taiwan_status.py',    '台股兩策略 A抱0052 / B輪動最強(全球信用費半哨)', True),
    ('leaders_status.py',   '台美股產業龍頭個股(衛星) leaders_status.json', True),
    ('generate_signals.py', '個股衛星信號 strategy_signals.json',        True),
    ('generate_evidence.py','回測實證 backtest_evidence.json',           True),
    ('strategy_menu.py',    '策略選單 strategy_menu.json',               False),
]


def run_gen(step, desc):
    path = os.path.join(HERE, step)
    if not os.path.exists(path):
        print(f"  ⏭️  略過 {step}(檔案不存在)")
        return None
    print(f"\n{'='*64}\n▶ 更新：{desc}\n  python {step}\n{'='*64}")
    try:
        r = subprocess.run([sys.executable, path], cwd=HERE,
                           capture_output=True, text=True, encoding='utf-8', timeout=900)
        if r.stdout:
            tail = r.stdout.rstrip().splitlines()
            print('\n'.join(tail[-6:]) if len(tail) > 6 else r.stdout.rstrip())
        if r.returncode != 0:
            print(f"  ⚠️ {step} 回傳碼 {r.returncode}"
                  + (f"：{r.stderr.strip().splitlines()[-1]}" if r.stderr.strip() else ''))
            return False
        return True
    except Exception as e:
        print(f"  ❌ {step} 執行失敗：{e}")
        return False


def port_open(port, host='127.0.0.1', timeout=0.4):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def ensure_server():
    """確保 web_server.py 在跑;沒跑就背景啟動。回傳 True=伺服器可用。"""
    if port_open(PORT):
        print(f"\n🌐 伺服器已在運行(localhost:{PORT})")
        return True
    server = os.path.join(HERE, 'web_server.py')
    if not os.path.exists(server):
        print("  ❌ 找不到 web_server.py,無法啟動伺服器")
        return False
    print("\n🚀 啟動本機伺服器 web_server.py(會開一個小黑窗,別關它=伺服器在跑)...")
    flags = subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0
    try:
        subprocess.Popen([sys.executable, server], cwd=HERE, creationflags=flags)
    except Exception as e:
        print(f"  ❌ 啟動伺服器失敗：{e}")
        return False
    for _ in range(25):   # 等埠開,最多 ~10 秒
        if port_open(PORT):
            print(f"  ✅ 伺服器就緒(localhost:{PORT})")
            return True
        time.sleep(0.4)
    print("  ⚠️ 伺服器啟動逾時,稍後再手動重新整理瀏覽器")
    return False


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        try: sys.stdout.reconfigure(encoding='utf-8')
        except Exception: pass

    fast = '--fast' in sys.argv
    print("🔄 一鍵更新儀表板（資料 → 伺服器 → 開啟）...")
    ok = 0; total = 0
    for step, desc, daily in GENERATORS:
        if fast and step == 'generate_signals.py':
            print(f"  ⏭️  --fast 模式:跳過個股 {step}")
            continue
        total += 1
        if run_gen(step, desc):
            ok += 1
    print(f"\n{'='*64}\n✅ 資料更新 {ok}/{total} 項完成。")

    if '--no-open' in sys.argv:
        print("（--no-open:不啟動伺服器/不開瀏覽器）")
        return

    if ensure_server():
        print(f"🌐 開啟儀表板：{URL}")
        webbrowser.open(URL)
        print("   （瀏覽器已開著的話,按 Ctrl+R / F5 重新整理即可看到最新數字）")
        print("   ⚠️ 那個小黑窗是伺服器,要看儀表板就別關它;關掉=儀表板會載入失敗。")
    else:
        print(f"⚠️ 伺服器沒起來。請手動在本資料夾執行：python web_server.py，再開 {URL}")


if __name__ == '__main__':
    main()
