"""
validate_universe.py - 生存者偏差壓力測試（洞#1）
======================================================
原股池是「今天的 AI 贏家」，回測天生灌水。這支腳本把同一套定案策略，
搬到一個「刻意中性、橫跨11板塊、沒有事後挑贏家」的大型股池(含表現平庸/
偏弱的名字)，用 SPY 當基準，看 edge 還剩多少、動能濾網是否仍有效。

判讀：
  - 若中性股池上 edge 仍在(Sharpe 正、動能濾網仍加分) → 策略是真的，能推廣。
  - 若崩掉 → 原結果只是 AI 主題的僥幸，需重新看待。

用法: python validate_universe.py
"""

import sys
import warnings
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import pandas as pd
import yfinance as yf

from validate_strategy import DEFAULT_RISK
from compare_strategies import build_windows, run_strategy_set
from exit_experiments import ExitScaledHybrid
from filter_experiments import (
    FilteredScaledExit, f_mom_positive, make_f_not_extended,
)

# 中性、跨板塊、未挑贏家的大型股池（含弱勢名字：INTC/PFE/BA/MO/T/VZ/NKE…）
DIVERSE_UNIVERSE = [
    # 科技
    'AAPL', 'MSFT', 'ORCL', 'CSCO', 'IBM', 'INTC', 'QCOM', 'TXN',
    # 通訊
    'GOOGL', 'META', 'NFLX', 'DIS', 'VZ', 'T', 'CMCSA',
    # 非必需消費
    'AMZN', 'HD', 'MCD', 'NKE', 'SBUX', 'LOW',
    # 必需消費
    'PG', 'KO', 'PEP', 'WMT', 'COST', 'CL', 'MO',
    # 醫療
    'JNJ', 'UNH', 'PFE', 'MRK', 'ABBV', 'TMO',
    # 金融
    'JPM', 'BAC', 'WFC', 'GS', 'MS', 'C', 'AXP',
    # 工業
    'CAT', 'BA', 'HON', 'UPS', 'RTX', 'DE',
    # 能源
    'XOM', 'CVX', 'COP', 'SLB', 'EOG',
    # 原物料 / 公用 / 房產
    'LIN', 'FCX', 'NEM', 'APD', 'NEE', 'DUK', 'SO', 'AMT', 'PLD', 'O',
]
BENCH = 'SPY'


def download(tickers, bench, period='3y'):
    print(f"📥 下載 {len(tickers)} 檔中性股池 + {bench} ({period})...")
    raw = yf.download(' '.join([bench] + tickers), period=period, interval='1d',
                      progress=False, group_by='ticker')
    stocks = {}
    for tk in tickers:
        try:
            df = raw[tk].dropna(how='all').copy()
            if len(df) > 260:
                stocks[tk] = df
        except Exception:
            pass
    bm = raw[bench].dropna(how='all').copy()
    if isinstance(bm.columns, pd.MultiIndex):
        bm.columns = bm.columns.get_level_values(0)
    print(f"✅ {len(stocks)} 檔有效 | {bench} {bm.index[0].date()} ~ {bm.index[-1].date()}")
    return bm, stocks


def final_strat():
    return [FilteredScaledExit(
        filters=[('mom+', f_mom_positive), ('not_ext', make_f_not_extended(1.08))],
        atr_target_mult=3.0, scale_frac=0.5, trail_mult=3.5)]


def no_mom_strat():
    # 只有 Ｅ(不追高)，拿掉動能濾網 → 看動能濾網的邊際貢獻是否在中性股池仍在
    return [FilteredScaledExit(
        filters=[('not_ext', make_f_not_extended(1.08))],
        atr_target_mult=3.0, scale_frac=0.5, trail_mult=3.5)]


def main():
    print(f"\n{'='*62}\n  生存者偏差壓力測試（中性跨板塊股池 vs 原AI股池）\n{'='*62}")
    bm, stocks = download(DIVERSE_UNIVERSE, BENCH, '3y')
    if not stocks:
        print('❌ 下載失敗'); return
    windows, d0, d1 = build_windows(bm, 12, 6, 6)
    print(f"  資料 {d0}~{d1}, {len(windows)} 個 OOS 視窗  (基準 {BENCH})\n")

    cfgs = [
        ('中性股池 · 定案(動能+不追高)', final_strat),
        ('中性股池 · 拿掉動能濾網',      no_mom_strat),
    ]
    sums = [run_strategy_set(n, mk, stocks, bm, windows, risk=DEFAULT_RISK) for n, mk in cfgs]

    print(f"\n  📊 中性股池結果 (3y 平均 OOS)")
    print(f"  {'配置':<26} {'Sharpe':>7} {'PF':>6} {'WR':>5} {'MDD':>7} {'交易':>5}")
    print(f"  {'-'*60}")
    for s in sums:
        print(f"  {s['name']:<26} {s['sharpe']:>+7.2f} {s['pf']:>6.2f} "
              f"{s['wr']:>4.0f}% {s['mdd']:>6.1f}% {s['trades']:>5}")
    d = sums[0]['sharpe'] - sums[1]['sharpe']
    print(f"\n  動能濾網在中性股池的邊際貢獻: Δ{d:+.2f} Sharpe")
    print(f"  對照: 原 AI 股池 3y 定案 Sharpe ≈ +1.9~2.2 (含heat上限)")
    print(f"  → 判讀: {'✅ edge 在中性股池仍在，策略可推廣，非AI僥幸' if sums[0]['sharpe']>0.7 and d>0 else '⚠️ 中性股池上 edge 明顯弱化，原結果含主題紅利'}")
    print(f"{'='*62}\n")


if __name__ == '__main__':
    main()
