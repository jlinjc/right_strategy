"""
web_server.py - Anti-Gravity Dashboard 伺服器
===============================================
功能：
  1. 靜態檔案伺服 (HTML/CSS/JS/JSON)
  2. LINE 推播 API (/api/send_line)
  3. 盤中自動刷新 3 分 K 線數據 (每 3 分鐘)
  4. 前端一鍵刷新 API (/api/refresh_charts)
"""

import os
import sys

if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import http.server
import socketserver
import json
import threading
import time
from datetime import datetime, timezone, timedelta

# Ensure parent dir is in sys.path so we can import scanner_base
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from scanner_base import send_line_notify

PORT = 8000
WEB_DIR = os.path.join(SCRIPT_DIR, 'Web_Dashboard')

# ============================================================
# 盤中自動刷新 3 分 K 線
# ============================================================
US_EASTERN = timezone(timedelta(hours=-4))  # EDT

def is_market_hours():
    """檢查是否在美股盤中時間 (ET 9:30-16:00, 週一到週五)"""
    now_et = datetime.now(US_EASTERN)
    if now_et.weekday() >= 5:  # 週末
        return False
    market_open = now_et.replace(hour=9, minute=25, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=5, second=0, microsecond=0)
    return market_open <= now_et <= market_close

def refresh_3m_charts():
    """重新下載 3 分 K 線數據 (不含日線，因為日線一天只需要一次)"""
    import subprocess
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔄 自動刷新 3 分 K 線數據...")
    try:
        subprocess.run(
            [sys.executable, os.path.join(SCRIPT_DIR, 'generate_chart_data.py')],
            cwd=SCRIPT_DIR,
            timeout=120,
        )
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ 3 分 K 數據已刷新")
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ 刷新失敗: {e}")

def auto_refresh_loop():
    """背景執行緒：盤中每 3 分鐘自動刷新圖表"""
    REFRESH_INTERVAL = 180  # 3 分鐘
    while True:
        time.sleep(REFRESH_INTERVAL)
        if is_market_hours():
            refresh_3m_charts()
        else:
            # 非盤中時間，降低檢查頻率
            time.sleep(60)

def download_single_chart(ticker):
    """即時下載單一股票的 K 線數據 (3分K + 日線)"""
    import yfinance as yf
    import pandas as pd
    charts_dir = os.path.join(WEB_DIR, 'charts')
    os.makedirs(charts_dir, exist_ok=True)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔍 即時搜尋: {ticker}")
    result = {'status': 'ok', 'ticker': ticker, 'has_3m': False, 'has_daily': False}

    try:
        # 3 分 K (5 天)
        df_1m = yf.download(ticker, period='5d', interval='1m', progress=False)
        if df_1m is not None and not df_1m.empty:
            # Handle multi-level columns from yfinance
            if isinstance(df_1m.columns, pd.MultiIndex):
                df_1m.columns = df_1m.columns.get_level_values(0)
            df_3m = df_1m.resample('3min').agg({
                'Open': 'first', 'High': 'max', 'Low': 'min',
                'Close': 'last', 'Volume': 'sum'
            }).dropna()
            data_3m = []
            for idx, row in df_3m.iterrows():
                data_3m.append({
                    'time': int(idx.timestamp()),
                    'open': round(float(row['Open']), 2),
                    'high': round(float(row['High']), 2),
                    'low': round(float(row['Low']), 2),
                    'close': round(float(row['Close']), 2),
                    'volume': int(row['Volume']) if not pd.isna(row['Volume']) else 0,
                })
            if data_3m:
                with open(os.path.join(charts_dir, f'{ticker}_3m.json'), 'w') as f:
                    json.dump(data_3m, f)
                result['has_3m'] = True
                print(f"  ✅ {ticker} 3分K: {len(data_3m)} 根")
    except Exception as e:
        print(f"  ⚠️ {ticker} 3分K 失敗: {e}")

    try:
        # 日線 (60 天)
        df_d = yf.download(ticker, period='60d', interval='1d', progress=False)
        if df_d is not None and not df_d.empty:
            if isinstance(df_d.columns, pd.MultiIndex):
                df_d.columns = df_d.columns.get_level_values(0)
            data_d = []
            for idx, row in df_d.iterrows():
                data_d.append({
                    'time': idx.strftime('%Y-%m-%d'),
                    'open': round(float(row['Open']), 2),
                    'high': round(float(row['High']), 2),
                    'low': round(float(row['Low']), 2),
                    'close': round(float(row['Close']), 2),
                    'volume': int(row['Volume']) if not pd.isna(row['Volume']) else 0,
                })
            if data_d:
                with open(os.path.join(charts_dir, f'{ticker}_daily.json'), 'w') as f:
                    json.dump(data_d, f)
                result['has_daily'] = True
                print(f"  ✅ {ticker} 日線: {len(data_d)} 根")
    except Exception as e:
        print(f"  ⚠️ {ticker} 日線失敗: {e}")

    return result

# ============================================================
# Web Server with API
# ============================================================
class APIHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def log_message(self, format, *args):
        """過濾 404 log — 不印出已知不存在的檔案"""
        msg = format % args
        if '404' in msg and any(f in msg for f in ['hod_data', 'orb_data', 'live_data']):
            return  # 靜默跳過
        super().log_message(format, *args)

    def do_POST(self):
        if self.path == '/api/send_line':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                data = json.loads(post_data.decode('utf-8'))
                message = data.get('message', '【Anti-Gravity 手動推播】')
                
                print(f"📡 收到前端推播請求，準備發送 LINE...")
                send_line_notify(message)
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status": "success"}')
            except Exception as e:
                print(f"⚠️ API 錯誤: {e}")
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status": "error"}')

        elif self.path == '/api/refresh_charts':
            # 手動觸發刷新
            try:
                threading.Thread(target=refresh_3m_charts, daemon=True).start()
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'status': 'ok',
                    'message': '圖表刷新已觸發',
                    'time': datetime.now().strftime('%H:%M:%S')
                }).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status": "error"}')

        elif self.path == '/api/watchlist':
            # 修改追蹤清單
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                action = data.get('action', '')
                ticker = data.get('ticker', '').upper().strip()

                from scanner_base import load_watchlist, save_watchlist
                stocks = load_watchlist()

                if action == 'add' and ticker and ticker not in stocks:
                    stocks.append(ticker)
                    save_watchlist(stocks)
                    print(f"➕ 已加入追蹤: {ticker}")
                elif action == 'remove' and ticker in stocks:
                    stocks.remove(ticker)
                    save_watchlist(stocks)
                    print(f"➖ 已移除追蹤: {ticker}")

                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok', 'stocks': stocks}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'error', 'message': str(e)}).encode('utf-8'))

        elif self.path == '/api/quick_chart':
            # 即時搜尋：下載任意股票的 3 分 K 和日線
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                ticker = data.get('ticker', '').upper().strip()
                if not ticker:
                    raise ValueError('No ticker')

                result = download_single_chart(ticker)

                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'error', 'message': str(e)}).encode('utf-8'))

        elif self.path == '/api/fund_inst':
            # 按需下載個股的 基本面 + 籌碼面 數據
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                ticker = data.get('ticker', '').upper().strip()
                if not ticker:
                    raise ValueError('No ticker')

                # 從原本的腳本匯入爬蟲函數
                import sys
                if SCRIPT_DIR not in sys.path:
                    sys.path.append(SCRIPT_DIR)
                from generate_fundamentals import fetch_fundamentals
                from generate_institutional import fetch_institutional

                print(f"[{datetime.now().strftime('%H:%M:%S')}] ⏳ 正在下載 {ticker} 基本面與籌碼面數據...")
                fund_data = fetch_fundamentals(ticker)
                inst_data = fetch_institutional(ticker)

                # 將下載的資料寫入全域 JSON 檔案以便前端存取 (這可以保持原有的讀取邏輯)
                # 基本面
                fund_path = os.path.join(WEB_DIR, 'fundamentals_data.json')
                fund_db = {'stocks': {}}
                if os.path.exists(fund_path):
                    with open(fund_path, 'r', encoding='utf-8') as f:
                        fund_db = json.load(f)
                if fund_data:
                    fund_db['stocks'][ticker] = fund_data
                with open(fund_path, 'w', encoding='utf-8') as f:
                    json.dump(fund_db, f, ensure_ascii=False)

                # 籌碼面
                inst_path = os.path.join(WEB_DIR, 'institutional_data.json')
                inst_db = {'stocks': {}}
                if os.path.exists(inst_path):
                    with open(inst_path, 'r', encoding='utf-8') as f:
                        inst_db = json.load(f)
                if inst_data:
                    inst_db['stocks'][ticker] = inst_data
                with open(inst_path, 'w', encoding='utf-8') as f:
                    json.dump(inst_db, f, ensure_ascii=False)

                print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ {ticker} 數據下載完成")

                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok'}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'error', 'message': str(e)}).encode('utf-8'))

        else:
            self.send_error(404, "Not Found")

    def do_GET(self):
        # GET /api/watchlist — 讀取追蹤清單
        if self.path == '/api/watchlist':
            try:
                from scanner_base import load_watchlist
                stocks = load_watchlist()
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'stocks': stocks}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status": "error"}')
        else:
            # 靜態檔案
            super().do_GET()

def start_server():
    os.chdir(WEB_DIR)

    # 啟動背景自動刷新執行緒
    refresh_thread = threading.Thread(target=auto_refresh_loop, daemon=True)
    refresh_thread.start()
    print(f"⏰ 盤中自動刷新已啟動 (每 3 分鐘)")

    import socket
    def get_local_ip():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('10.255.255.255', 1))
            ip = s.getsockname()[0]
        except Exception:
            ip = '127.0.0.1'
        finally:
            s.close()
        return ip

    local_ip = get_local_ip()

    # Ensure address reuse
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), APIHandler) as httpd:
        print(f"🌐 啟動自訂 Web Server (支援 API)")
        print(f"   💻 本機網址 (自己用)   → http://localhost:{PORT}")
        print(f"   📱 區域網路 (手機可用) → http://{local_ip}:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()

if __name__ == '__main__':
    start_server()
