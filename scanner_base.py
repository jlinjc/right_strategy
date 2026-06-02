# [LOCAL VERSION DIFF]: 新增了資金控管與風險管理模組 (calc_atr, calculate_market_breadth 計算大盤廣度, calculate_position 計算部位大小與停損)。
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
    return # [USER REQUEST]: 依據使用者要求，暫停所有 LINE 推播功能
    
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
    # 1. 先進半導體與封裝 (AI Semiconductors & Packaging)
    # 1. 先進半導體與封裝 (AI Semiconductors & Packaging)
    "NVDA", "AMD", "TSM", "AVGO", "ARM",
    # 2. 矽光子與高速光通信 (Silicon Photonics & Optics)
    "COHR", "LITE", "CLS", "FN", "CAMT",
    # 3. AI伺服器與高速存儲 (AI Servers & Storage)
    "SMCI", "DELL", "ANET", "PSTG", "WDC",
    # 4. 液冷基建與精密空調 (Cooling & HVAC Infrastructure)
    "VRT", "MOD", "FIX", "EME", "JCI",
    # 5. AI電力、核能與SMR (AI Power & Grid & SMR)
    "CEG", "VST", "GEV", "ETN", "SMR",
    # 6. AI軟體、智慧代理與超大市值 (AI SaaS & Hyperscalers)
    "PLTR", "APP", "MSFT", "GOOGL", "META",
    # 7. 減肥藥與生技巨頭 (GLP-1 Weight Loss & Biotech)
    "LLY", "NVO", "VKTX", "TMDX", "CRSP",
    # 8. 低軌衛星與太空軍工 (Space & Satellites & Defense)
    "RKLB", "LUNR", "ASTS", "GE", "LMT",
    # 9. 自動駕駛與智慧機器人 (Autonomous & Robotics)
    "TSLA", "UBER", "SYM", "ISRG", "ROK",
    # 10. 網路安全與未來金融科技 (Cybersecurity & Fintech & Crypto)
    "CRWD", "PANW", "NET", "COIN", "HOOD",
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

# ============================================================
# 風控與倉位管理模組 (Risk & Position Sizing)
# ============================================================
TOTAL_CAPITAL = 10000        # 用戶總資金 (預設 $10,000)
RISK_PER_TRADE = 0.02        # 單筆交易願意承受的最大虧損比例 (2%)

def calc_atr(high, low, close, window=14):
    """計算 Average True Range (ATR)"""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window).mean()

def calculate_market_breadth():
    """
    計算大盤廣度：AI_TECH_STOCKS 中股價 > 50MA 的比例。
    使用 yfinance 下載過去 75 天的日 K 線。
    """
    try:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 正在計算大盤廣度 (Market Breadth)...")
        tickers_str = " ".join(AI_TECH_STOCKS)
        # 下載 75 天以確保扣除假日後有足夠的 50 天計算 50MA
        df = yf.download(tickers_str, period="75d", interval="1d", progress=False, group_by='ticker')
        if df.empty:
            print("⚠️ 大盤廣度計算失敗：下載數據為空，預設廣度 50%")
            return 50.0
            
        above_50ma_count = 0
        valid_stocks_count = 0
        
        for ticker in AI_TECH_STOCKS:
            try:
                if ticker in df.columns.levels[0]:
                    ticker_df = df[ticker].dropna(how='all')
                    if len(ticker_df) >= 50:
                        close = ticker_df['Close'].iloc[-1]
                        ma50 = ticker_df['Close'].rolling(window=50).mean().iloc[-1]
                        if not pd.isna(close) and not pd.isna(ma50):
                            valid_stocks_count += 1
                            if close > ma50:
                                above_50ma_count += 1
            except Exception:
                pass
                
        if valid_stocks_count > 0:
            breadth_pct = (above_50ma_count / valid_stocks_count) * 100
            print(f"✅ 大盤廣度計算完成: {breadth_pct:.1f}% ({above_50ma_count}/{valid_stocks_count} 檔股票高於 50MA)")
            return breadth_pct
        else:
            print("⚠️ 無有效股票數據計算大盤廣度，預設廣度 50%")
            return 50.0
    except Exception as e:
        print(f"⚠️ 計算大盤廣度時發生錯誤: {e}")
        return 50.0

def calculate_position(entry_price, stop_loss_price, custom_risk_pct=None):
    """
    根據風險比例計算建議買入股數。
    回傳: (建議買入股數, 總買入金額, 實際風險金額)
    """
    if entry_price <= 0 or stop_loss_price <= 0 or stop_loss_price >= entry_price:
        return 0, 0.0, 0.0
        
    risk_pct = custom_risk_pct if custom_risk_pct is not None else RISK_PER_TRADE
    risk_amount = TOTAL_CAPITAL * risk_pct
    stop_distance = entry_price - stop_loss_price
    
    # 根據停損距離計算股數
    shares = int(risk_amount / stop_distance)
    
    # 鐵律：單筆交易總金額不得超過本金의 20%
    max_capital_per_trade = TOTAL_CAPITAL * 0.20
    max_shares = int(max_capital_per_trade / entry_price)
    
    shares = min(shares, max_shares)
    shares = max(shares, 0)
    
    total_cost = shares * entry_price
    actual_risk = shares * stop_distance
    
    return shares, total_cost, actual_risk


def get_market_regime():
    """
    整合大盤廣度 + QQQ 均線判斷，回傳統一的市場環境物件。
    用於所有策略的進場前環境檢查。
    """
    import yfinance as yf

    result = {
        'breadth': 50.0,
        'breadth_label': '🟡 震盪',
        'qqq_price': 0,
        'qqq_20ma': 0,
        'qqq_50ma': 0,
        'qqq_vs_20ma': 'unknown',
        'qqq_vs_50ma': 'unknown',
        'regime': 'half_risk',
        'risk_per_trade': 0.01,
        'max_positions': 3,
        'message': '計算中...',
    }

    # 1. 計算大盤廣度
    breadth = calculate_market_breadth()
    result['breadth'] = breadth

    # 2. 下載 QQQ 日線計算均線
    try:
        qqq_df = yf.download(BENCHMARK, period="75d", interval="1d", progress=False)
        if qqq_df is not None and not qqq_df.empty:
            if isinstance(qqq_df.columns, pd.MultiIndex):
                qqq_df.columns = qqq_df.columns.get_level_values(0)
            qqq_close = float(qqq_df['Close'].iloc[-1])
            qqq_20ma = float(qqq_df['Close'].rolling(20).mean().iloc[-1]) if len(qqq_df) >= 20 else 0
            qqq_50ma = float(qqq_df['Close'].rolling(50).mean().iloc[-1]) if len(qqq_df) >= 50 else 0

            result['qqq_price'] = round(qqq_close, 2)
            result['qqq_20ma'] = round(qqq_20ma, 2)
            result['qqq_50ma'] = round(qqq_50ma, 2)
            result['qqq_vs_20ma'] = 'above' if qqq_close > qqq_20ma else 'below'
            result['qqq_vs_50ma'] = 'above' if (qqq_50ma > 0 and qqq_close > qqq_50ma) else 'below'
    except Exception as e:
        print(f"⚠️ QQQ 均線計算失敗: {e}")

    # 3. 綜合判定
    qqq_below_50ma = result['qqq_vs_50ma'] == 'below'

    if breadth > 60 and not qqq_below_50ma:
        result['breadth_label'] = '🟢 強勢多頭'
        result['regime'] = 'full_risk'
        result['risk_per_trade'] = 0.02
        result['max_positions'] = 6
        result['message'] = '大盤環境良好，正常交易 (單筆風險 2%，最多 6 檔)'
    elif breadth >= 40 and not qqq_below_50ma:
        result['breadth_label'] = '🟡 震盪行情'
        result['regime'] = 'half_risk'
        result['risk_per_trade'] = 0.01
        result['max_positions'] = 3
        result['message'] = '震盪行情，減半交易 (單筆風險 1%，最多 3 檔)'
    else:
        result['breadth_label'] = '🔴 防守行情'
        result['regime'] = 'no_risk'
        result['risk_per_trade'] = 0.0
        result['max_positions'] = 0
        result['message'] = '大盤弱勢，暫停做多！持有現金等待轉強。'

    # QQQ < 50MA 強制降級
    if qqq_below_50ma and result['regime'] != 'no_risk':
        result['regime'] = 'no_risk'
        result['risk_per_trade'] = 0.0
        result['max_positions'] = 0
        result['message'] = '⚠️ QQQ 跌破 50MA，暫停所有新倉！'
        result['breadth_label'] = '🔴 QQQ < 50MA'

    print(f"✅ 市場環境判定: {result['breadth_label']} | 廣度 {breadth:.1f}% | QQQ ${result['qqq_price']} vs 20MA ${result['qqq_20ma']} / 50MA ${result['qqq_50ma']}")
    return result


