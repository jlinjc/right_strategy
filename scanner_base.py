"""
scanner_base.py - 美股當沖掃描器共用模組
共用的資料下載、股票清單與工具函式，供 HOD / ORB 掃描器調用。
修改股票清單只需改這裡一次，兩個策略自動同步。
"""

import yfinance as yf
import pandas as pd
from datetime import datetime
import warnings
import os
import requests
warnings.filterwarnings('ignore')

import os
from dotenv import load_dotenv

# 載入 .env 檔案
load_dotenv()

# ============================================================
# LINE Bot (Messaging API) 設定
# ============================================================
# 從環境變數讀取 Token，避免外洩至 GitHub
LINE_BOT_TOKEN = os.getenv("LINE_BOT_TOKEN")

def upload_image_to_catbox(image_path):
    """將本地圖片上傳至 Catbox (免 API Key 的免費圖床) 並取得 HTTPS URL"""
    import requests
    url = "https://catbox.moe/user/api.php"
    data = {"reqtype": "fileupload"}
    try:
        with open(image_path, "rb") as f:
            files = {"fileToUpload": f}
            res = requests.post(url, data=data, files=files)
        if res.status_code == 200:
            return res.text
    except Exception as e:
        print(f"圖片上傳失敗: {e}")
    return None

def send_line_notify(message, image_path=None):
    """發送 LINE Bot 廣播通知 (支援圖片)"""
    if not LINE_BOT_TOKEN:
        return
        
    messages = [{"type": "text", "text": message}]
    
    # 如果有圖片，先上傳到雲端圖床取得 URL (LINE Bot 規定必須用 HTTPS URL)
    if image_path and os.path.exists(image_path):
        img_url = upload_image_to_catbox(image_path)
        if img_url:
            messages.append({
                "type": "image",
                "originalContentUrl": img_url,
                "previewImageUrl": img_url
            })
            
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_BOT_TOKEN}"
    }
    data = {"messages": messages}
    
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code != 200:
            print(f"⚠️ LINE Bot 通知發送失敗，狀態碼: {response.status_code}, 錯誤訊息: {response.text}")
    except Exception as e:
        print(f"⚠️ LINE Bot 通知發生錯誤: {e}")

# ============================================================
# Dashboard JSON 輸出
# ============================================================
DASHBOARD_DIR = os.path.join(os.path.dirname(__file__), 'Web_Dashboard')

def save_dashboard_data(filename, data):
    """將掃描結果存成 JSON，供 Web Dashboard 讀取"""
    import json
    if not os.path.exists(DASHBOARD_DIR):
        os.makedirs(DASHBOARD_DIR)
        
    filepath = os.path.join(DASHBOARD_DIR, filename)
    data['last_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Failed to save dashboard data {filename}: {e}")

# ============================================================
# 監控清單 & 常數（只在這裡維護一份）
# ============================================================
BENCHMARK = "QQQ"

_DEFAULT_STOCKS = [
    # 半導體
    "NVDA", "AMD", "TSM", "AVGO", "MU", "QCOM", "ARM", "MRVL",
    "AMAT", "LRCX", "KLAC", "TXN", "INTC", "MPWR",
    # 軟體 & 雲端
    "MSFT", "GOOGL", "AMZN", "META", "AAPL", "IBM",
    "PLTR", "CRM", "ORCL", "NOW", "SNOW", "DDOG", "MDB",
    "ADBE", "INTU", "PATH", "APP",
    # 資安
    "NET", "CRWD", "PANW", "FTNT", "ZS", "OKTA",
    # 伺服器 & 網通
    "SMCI", "DELL", "HPE", "ANET", "PSTG", "NTAP",
    # AI 電力 & 基建
    "VRT", "ETN", "PWR", "CEG", "NEE", "GE", "DUK",
    # 其他
    "TSLA", "UBER", "SYM",
]

WATCHLIST_FILE = os.path.join(os.path.dirname(__file__), 'watchlist.json')

def load_watchlist():
    """從 watchlist.json 載入追蹤清單，不存在則用預設清單"""
    import json
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('stocks', _DEFAULT_STOCKS)
        except Exception:
            pass
    return list(_DEFAULT_STOCKS)

def save_watchlist(stocks):
    """將追蹤清單存回 watchlist.json"""
    import json
    with open(WATCHLIST_FILE, 'w', encoding='utf-8') as f:
        json.dump({'stocks': stocks, 'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}, f, ensure_ascii=False, indent=2)

AI_TECH_STOCKS = load_watchlist()

SURGE_THRESHOLD = 0.005          # 單根 3分K >= 0.5% 觸發飆升警報
RESILIENT_MIN_DAY_RET = 0.001    # 抗跌股最低日漲幅 >= 0.1%（過濾沒在動的股票）

# ============================================================
# 資料下載（P1: 兩個策略共用同一份邏輯）
# ============================================================
def get_market_and_stocks_3m(ticker_list=None, benchmark=None):
    """下載大盤與個股的 1 分鐘 K 線，轉換為 3 分鐘 K 線。"""
    if ticker_list is None:
        ticker_list = AI_TECH_STOCKS
    if benchmark is None:
        benchmark = BENCHMARK

    all_tickers = [benchmark] + ticker_list
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 正在下載大盤與 {len(ticker_list)} 檔個股最新 K 線...")

    tickers_str = " ".join(all_tickers)
    df = yf.download(tickers_str, period="5d", interval="1m", progress=False, group_by='ticker')

    if df.empty:
        print("❌ 下載失敗，請檢查網路連線。")
        return None, {}

    result_dict = {}
    failed = []
    for ticker in all_tickers:
        try:
            if ticker in df.columns.levels[0]:
                ticker_df = df[ticker].dropna(how='all')
                if not ticker_df.empty:
                    df_3m = ticker_df.resample('3min').agg({
                        'Open': 'first', 'High': 'max', 'Low': 'min',
                        'Close': 'last', 'Volume': 'sum'
                    }).dropna()
                    result_dict[ticker] = df_3m
        except Exception:
            failed.append(ticker)

    if failed:
        print(f"⚠️ 以下 {len(failed)} 檔股票資料異常，已跳過: {', '.join(failed)}")

    benchmark_df = result_dict.pop(benchmark, None)
    print(f"✅ 成功載入 {len(result_dict)} 檔個股資料。")
    return benchmark_df, result_dict

# ============================================================
# 工具函式
# ============================================================
def calc_volume_ratio(candle_volume, today_avg_volume):
    """計算成交量倍率：當根成交量 / 今日平均成交量"""
    if today_avg_volume > 0:
        return candle_volume / today_avg_volume
    return 0.0
