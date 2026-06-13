"""
start_strategy.py - 定案策略作戰中心 一鍵啟動
=================================================
精簡啟動器，只跑這套「右側動能拉回 + 分批出場」定案系統：
  1. generate_signals.py  → 今日進場信號卡 + 大盤 regime
  2. generate_evidence.py → 回測實證記錄
  3. web_server.py        → 本地伺服器
  4. 自動開到作戰中心頁面

用法：
  python start_strategy.py
  （只想更新資料不開站：python start_strategy.py --data-only）
"""

import os
import sys
import time
import subprocess
import threading
import webbrowser

os.environ['PYTHONIOENCODING'] = 'utf-8'
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
URL = 'http://localhost:8000/strategy_dashboard.html'


def run(script):
    print(f"\n▶ {script}")
    subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, script)], cwd=SCRIPT_DIR)


def main():
    data_only = '--data-only' in sys.argv
    print("=" * 52)
    print("  Anti-Gravity 作戰中心 — 右側動能拉回 + 分批出場")
    print("=" * 52)

    # 1+2. 更新資料
    run('generate_signals.py')
    run('generate_evidence.py')

    if data_only:
        print("\n✅ 資料已更新（未開站）。")
        return

    # 3. 啟動伺服器
    print("\n🌐 啟動伺服器 → " + URL)
    subprocess.Popen([sys.executable, os.path.join(SCRIPT_DIR, 'web_server.py')], cwd=SCRIPT_DIR)

    # 4. 開瀏覽器
    def _open():
        time.sleep(2)
        webbrowser.open(URL)
    threading.Thread(target=_open, daemon=True).start()

    print("\n✅ 啟動完成！")
    print("👉 作戰中心: " + URL)
    print("👉 Ctrl+C 停止")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 已停止。")


if __name__ == '__main__':
    main()
