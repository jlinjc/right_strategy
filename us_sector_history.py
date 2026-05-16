"""
us_sector_history.py - 產業板塊資金輪動歷史
=============================================
讀取已下載的日線 JSON，計算各板塊的累積報酬走勢，
輸出 sector_history.json 供前端繪製輪動趨勢圖。

此腳本在 start_dashboard.py 產出圖表資料後自動執行。
"""

import os
import sys
import json
from datetime import datetime

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHARTS_DIR = os.path.join(SCRIPT_DIR, 'Web_Dashboard', 'charts')
OUTPUT_PATH = os.path.join(SCRIPT_DIR, 'Web_Dashboard', 'sector_history.json')

# ── 板塊定義 (與前端 SECTOR_MAP 完全一致) ──
SECTOR_MAP = {
    "雲端巨頭 (Hyperscalers)": ["MSFT", "GOOGL", "AMZN", "META", "AAPL"],
    "晶片設計 (IC Design)": ["NVDA", "AMD", "QCOM", "ARM"],
    "晶圓代工與IDM (Foundry)": ["TSM", "INTC"],
    "特用晶片 (Memory/Net/Power)": ["AVGO", "MRVL", "MU", "TXN", "MPWR"],
    "半導體設備 (Semi Equipment)": ["AMAT", "LRCX", "KLAC"],
    "AI伺服器與硬體 (AI Servers)": ["SMCI", "DELL", "HPE"],
    "網通與儲存設備 (Network & Storage)": ["ANET", "NTAP", "PSTG"],
    "資料中心基建與散熱 (Infra & Cooling)": ["VRT", "ETN", "PWR"],
    "公用事業與電力 (Utilities & Power)": ["CEG", "NEE", "GE", "DUK"],
    "資料庫與AI分析 (Data & AI Platforms)": ["PLTR", "SNOW", "MDB", "DDOG"],
    "企業軟體 (Enterprise SaaS)": ["CRM", "NOW", "ORCL", "ADBE", "INTU", "IBM"],
    "網路安全 (Cybersecurity)": ["CRWD", "PANW", "FTNT", "ZS", "NET", "OKTA"],
    "自動化與應用 (Automation & Apps)": ["TSLA", "PATH", "APP", "SYM", "UBER"],
}

# 每個板塊的配色 (前端繪圖用)
SECTOR_COLORS = {
    "雲端巨頭 (Hyperscalers)": "#3b82f6",
    "晶片設計 (IC Design)": "#ef4444",
    "晶圓代工與IDM (Foundry)": "#f59e0b",
    "特用晶片 (Memory/Net/Power)": "#8b5cf6",
    "半導體設備 (Semi Equipment)": "#ec4899",
    "AI伺服器與硬體 (AI Servers)": "#f97316",
    "網通與儲存設備 (Network & Storage)": "#14b8a6",
    "資料中心基建與散熱 (Infra & Cooling)": "#06b6d4",
    "公用事業與電力 (Utilities & Power)": "#84cc16",
    "資料庫與AI分析 (Data & AI Platforms)": "#a855f7",
    "企業軟體 (Enterprise SaaS)": "#6366f1",
    "網路安全 (Cybersecurity)": "#10b981",
    "自動化與應用 (Automation & Apps)": "#64748b",
}


def load_daily_data(ticker):
    """讀取單檔股票的日線 JSON"""
    filepath = os.path.join(CHARTS_DIR, f"{ticker}_daily.json")
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except Exception:
        return None


def calculate_sector_history():
    """計算各板塊的歷史累積報酬"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 📊 正在計算產業板塊輪動歷史...")

    # ── 1. 載入所有股票的日線資料 ──
    # 格式: { ticker: { date_str: close_price } }
    all_closes = {}
    all_dates = set()

    for sector, tickers in SECTOR_MAP.items():
        for ticker in tickers:
            data = load_daily_data(ticker)
            if not data:
                continue
            closes = {}
            for candle in data:
                date_str = candle['time'] if isinstance(candle['time'], str) else datetime.utcfromtimestamp(candle['time']).strftime('%Y-%m-%d')
                closes[date_str] = candle['close']
                all_dates.add(date_str)
            if closes:
                all_closes[ticker] = closes

    if not all_dates:
        print("  ⚠️ 找不到任何日線資料")
        return

    # ── 2. 排序日期 ──
    sorted_dates = sorted(all_dates)

    # ── 3. 計算各板塊每天的平均報酬 ──
    result_sectors = {}

    for sector, tickers in SECTOR_MAP.items():
        # 收集此板塊所有股票在所有日期的收盤價
        sector_daily_returns = []  # 每天的平均報酬率

        # 找出有資料的股票
        valid_tickers = [t for t in tickers if t in all_closes]
        if not valid_tickers:
            continue

        # 計算累積報酬：以第一天的收盤價為基準
        cumulative = []
        for date in sorted_dates:
            returns = []
            for ticker in valid_tickers:
                if date not in all_closes[ticker]:
                    continue
                # 找到該股票在 sorted_dates 中第一個有資料的收盤價
                first_close = None
                for d in sorted_dates:
                    if d in all_closes[ticker]:
                        first_close = all_closes[ticker][d]
                        break
                if first_close and first_close > 0:
                    ret = (all_closes[ticker][date] / first_close - 1) * 100
                    returns.append(ret)

            if returns:
                avg_ret = sum(returns) / len(returns)
                cumulative.append(round(avg_ret, 2))
            elif cumulative:
                cumulative.append(cumulative[-1])  # 沒資料就沿用前一天
            else:
                cumulative.append(0)

        # 計算近期動能指標
        latest_ret = cumulative[-1] if cumulative else 0
        ret_5d = cumulative[-1] - cumulative[-6] if len(cumulative) >= 6 else cumulative[-1] - cumulative[0] if cumulative else 0
        ret_10d = cumulative[-1] - cumulative[-11] if len(cumulative) >= 11 else cumulative[-1] - cumulative[0] if cumulative else 0

        result_sectors[sector] = {
            "cumulative": cumulative,
            "color": SECTOR_COLORS.get(sector, "#94a3b8"),
            "latest": round(latest_ret, 2),
            "ret_5d": round(ret_5d, 2),
            "ret_10d": round(ret_10d, 2),
            "stock_count": len(valid_tickers),
        }

    # ── 4. 輸出 JSON ──
    output = {
        "dates": sorted_dates,
        "sectors": result_sectors,
        "last_updated": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"  ✅ 板塊輪動歷史已輸出: {len(result_sectors)} 個板塊, {len(sorted_dates)} 天")
    for sector, data in sorted(result_sectors.items(), key=lambda x: x[1]['latest'], reverse=True):
        sign = '+' if data['latest'] > 0 else ''
        print(f"     {sector}: {sign}{data['latest']}%  (5d: {'+' if data['ret_5d']>0 else ''}{data['ret_5d']}%)")


if __name__ == '__main__':
    calculate_sector_history()
