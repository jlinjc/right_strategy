"""
test_upgrade_scanners.py - 測試與驗證升級後的三大策略 (HOD, TD9, MA Pullback)
一次性執行所有策略掃描，驗證大盤廣度 (Market Breadth) 與高勝率多因子的過濾邏輯是否正確，並檢查終端格式。
"""

import sys
import os
from datetime import datetime

# 確保輸出支援 UTF-8 以防 emoji 亂碼崩潰
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# 導入升級後的策略模組
from scanner_base import calculate_market_breadth, get_market_and_stocks_3m
from us_scanner_hod import scan_latest_kline_hod
from us_scanner_td9 import scan_td9
from us_scanner_ma import scan_ma_pullback

def test_market_breadth():
    print("\n=============================================")
    print("🧪 1. 測試大盤廣度計算 (Market Breadth)")
    print("=============================================")
    try:
        breadth = calculate_market_breadth()
        print(f"📊 最終大盤廣度結果: {breadth:.2f}%")
        if breadth > 60:
            print("🟢 大盤狀態：多頭行情 (預設曝險 100%)")
        elif breadth >= 40:
            print("🟡 大盤狀態：震盪行情 (預設曝險自動減半至 50%)")
        else:
            print("🔴 大盤狀態：熊市防禦 (預設曝險 0%，暫停開倉)")
    except Exception as e:
        print(f"❌ 測試大盤廣度失敗: {e}")

def test_hod_scanner():
    print("\n=============================================")
    print("🧪 2. 測試 HOD 日內當沖突破 (單次掃描)")
    print("=============================================")
    try:
        market_df, stocks_dict = get_market_and_stocks_3m()
        if market_df is not None and not market_df.empty:
            latest_time = market_df.index[-1]
            print(f"📊 最新 K 線時間: {latest_time.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # 執行掃描
            alerts, triggered = scan_latest_kline_hod(market_df, stocks_dict, target_time=latest_time)
            
            print(f"\n✅ 掃描完成。觸發警報數: {len(alerts)}")
            for a in alerts[:3]:
                print(f"\n--- 警報 Mockup 預覽 ---")
                print(a)
        else:
            print("❌ 無法取得當沖 3m K 線資料。")
    except Exception as e:
        print(f"❌ 測試 HOD 掃描器失敗: {e}")

def test_td9_scanner():
    print("\n=============================================")
    print("🧪 3. 測試 TD9 下跌竭盡 (單次掃描)")
    print("=============================================")
    try:
        alerts, latest_date, _ = scan_td9()
        print(f"\n✅ 掃描完成。觸發警報數: {len(alerts) if alerts else 0}")
        if alerts:
            for a in alerts[:3]:
                print(f"\n--- 警報 Mockup 預覽 ---")
                print(a)
    except Exception as e:
        print(f"❌ 測試 TD9 掃描器失敗: {e}")

def test_ma_scanner():
    print("\n=============================================")
    print("🧪 4. 測試 MA 均線回踩強勢股 (單次掃描)")
    print("=============================================")
    try:
        alerts, latest_date, _ = scan_ma_pullback()
        print(f"\n✅ 掃描完成。觸發警報數: {len(alerts) if alerts else 0}")
        if alerts:
            for a in alerts[:3]:
                print(f"\n--- 警報 Mockup 預覽 ---")
                print(a)
    except Exception as e:
        print(f"❌ 測試 MA 掃描器失敗: {e}")

if __name__ == "__main__":
    start_time = datetime.now()
    print("🚀 啟動 Anti-Gravity 三大策略升級版整合測試...")
    
    test_market_breadth()
    test_hod_scanner()
    test_td9_scanner()
    test_ma_scanner()
    
    end_time = datetime.now()
    print(f"\n🏁 整合測試結束。總耗時: {end_time - start_time}")
