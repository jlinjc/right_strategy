"""
start_dashboard.py - Anti-Gravity 一鍵啟動器
==============================================
同時啟動：
  1. 產出最新圖表 K 線 JSON (日線 + 3分K)
  2. 所有 Python 美股掃描引擎 (HOD / ORB / TD9 / MA)
  3. Web Dashboard 本地伺服器 (http://localhost:8000)

用法：
  cd C:\\Users\\Jason\\Desktop\\Anti
  python start_dashboard.py
"""

import subprocess
import sys
import os

os.environ['PYTHONIOENCODING'] = 'utf-8'

if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import time
import webbrowser
import threading

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_DIR = os.path.join(SCRIPT_DIR, 'Web_Dashboard')


def generate_charts():
    """先產出圖表 JSON 資料"""
    print("📊 正在產出圖表資料 (日線 + 3分K)...")
    subprocess.run(
        [sys.executable, os.path.join(SCRIPT_DIR, 'generate_chart_data.py')],
        cwd=SCRIPT_DIR,
    )


def start_web_server():
    """啟動 Python 內建 HTTP 伺服器"""
    print("🌐 啟動 Web Dashboard (含 API) → http://localhost:8000")
    subprocess.Popen(
        [sys.executable, os.path.join(SCRIPT_DIR, 'web_server.py')],
        cwd=SCRIPT_DIR,
    )


def start_scanners():
    """啟動所有掃描器"""
    print("🚀 啟動美股掃描引擎...")
    subprocess.Popen(
        [sys.executable, os.path.join(SCRIPT_DIR, 'run_all.py')],
        cwd=SCRIPT_DIR,
    )


def open_browser():
    """延遲 2 秒後自動打開瀏覽器"""
    time.sleep(2)
    webbrowser.open('http://localhost:8000')


if __name__ == '__main__':
    print("=" * 50)
    print("  Anti-Gravity 戰情室 — 一鍵啟動")
    print("=" * 50)
    print()

    os.makedirs(DASHBOARD_DIR, exist_ok=True)

    # 1. 產出圖表資料
    generate_charts()

    # 2. 產出回測資料
    print("📈 正在產出訊號回測資料...")
    subprocess.run(
        [sys.executable, os.path.join(SCRIPT_DIR, 'backtest_signals.py')],
        cwd=SCRIPT_DIR,
    )

    # 2.5. 產出板塊輪動歷史
    print("🏛️ 正在計算產業板塊輪動...")
    subprocess.run(
        [sys.executable, os.path.join(SCRIPT_DIR, 'us_sector_history.py')],
        cwd=SCRIPT_DIR,
    )

    # 2.6. 產出暴風動能選股策略
    print("🚀 正在執行暴風動能策略篩選...")
    subprocess.run(
        [sys.executable, os.path.join(SCRIPT_DIR, 'us_momentum_scanner.py')],
        cwd=SCRIPT_DIR,
    )

    # 基本面與籌碼面改為前端按需下載 (On-demand)

    # 3. 啟動網頁伺服器
    start_web_server()

    # 4. 啟動掃描器
    start_scanners()

    # 4. 自動打開瀏覽器
    threading.Thread(target=open_browser, daemon=True).start()

    print()
    print("✅ 全部啟動完成！")
    print("👉 瀏覽器: http://localhost:8000")
    print("👉 按 Ctrl+C 停止所有服務")
    print()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 已停止所有服務。")
