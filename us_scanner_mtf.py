import yfinance as yf
import pandas as pd
import json
import os
import time
from scanner_base import USStockScanner

class MTFScanner(USStockScanner):
    def __init__(self):
        super().__init__()
        self.output_file = os.path.join("Web_Dashboard", "mtf_trend.json")

    def run(self):
        while True:
            print(f"🔄 [MTF 趨勢引擎] 正在抓取 15m 與 60m 資料，判定大時區趨勢...")
            results = {}
            tickers_str = " ".join(self.tracked_tickers)
            
            try:
                # 抓取 60m 資料 (1h) - 取近 1 個月資料以確保 MA50 能算出來
                df_60m = yf.download(tickers_str, period="1mo", interval="1h", progress=False, group_by='ticker')
                # 抓取 15m 資料 - 取近 1 個月資料
                df_15m = yf.download(tickers_str, period="1mo", interval="15m", progress=False, group_by='ticker')

                for ticker in self.tracked_tickers:
                    res = {"15m": "震盪", "60m": "震盪"}
                    
                    # 處理 60m
                    try:
                        tk_df_60 = df_60m[ticker].dropna(how='all').copy() if isinstance(df_60m.columns, pd.MultiIndex) else df_60m.dropna(how='all').copy()
                        if len(tk_df_60) >= 50:
                            tk_df_60['20MA'] = tk_df_60['Close'].rolling(window=20).mean()
                            tk_df_60['50MA'] = tk_df_60['Close'].rolling(window=50).mean()
                            last_close = float(tk_df_60['Close'].iloc[-1])
                            last_20ma = float(tk_df_60['20MA'].iloc[-1])
                            last_50ma = float(tk_df_60['50MA'].iloc[-1])
                            
                            if last_close > last_20ma and last_20ma > last_50ma:
                                res["60m"] = "多頭"
                            elif last_close < last_20ma and last_20ma < last_50ma:
                                res["60m"] = "空頭"
                    except Exception:
                        pass

                    # 處理 15m
                    try:
                        tk_df_15 = df_15m[ticker].dropna(how='all').copy() if isinstance(df_15m.columns, pd.MultiIndex) else df_15m.dropna(how='all').copy()
                        if len(tk_df_15) >= 50:
                            tk_df_15['20MA'] = tk_df_15['Close'].rolling(window=20).mean()
                            tk_df_15['50MA'] = tk_df_15['Close'].rolling(window=50).mean()
                            last_close = float(tk_df_15['Close'].iloc[-1])
                            last_20ma = float(tk_df_15['20MA'].iloc[-1])
                            last_50ma = float(tk_df_15['50MA'].iloc[-1])
                            
                            if last_close > last_20ma and last_20ma > last_50ma:
                                res["15m"] = "多頭"
                            elif last_close < last_20ma and last_20ma < last_50ma:
                                res["15m"] = "空頭"
                    except Exception:
                        pass
                                
                    results[ticker] = res

                # 確保目錄存在
                os.makedirs(os.path.dirname(self.output_file), exist_ok=True)
                with open(self.output_file, 'w', encoding='utf-8') as f:
                    json.dump(results, f, ensure_ascii=False)
                print(f"✅ [MTF 趨勢引擎] 更新完成，大時區狀態已寫入 {self.output_file}")
            except Exception as e:
                print(f"⚠️ [MTF 趨勢引擎] 更新失敗: {e}")

            # 每 15 分鐘更新一次
            time.sleep(15 * 60)

if __name__ == "__main__":
    scanner = MTFScanner()
    scanner.run()
