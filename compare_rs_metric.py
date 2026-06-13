"""
compare_rs_metric.py - RS 指標定義優化（洞#3）
=================================================
固定「廣股池 + RS前20% + 定案進出場」，只換 RS 分數的「定義」，找最強的 RS 指標。
全部 price-based、point-in-time、無 look-ahead。3y SPY 基準。

RS 定義候選：
  blend   現行：0.4×3M+0.3×6M+0.3×12M
  mom126  純 6 個月
  mom252  純 12 個月
  ibd     IBD 4 季加權
  12m1m   學術經典 12-1（跳過最近1月）
  voladj  風險調整動能（6M報酬/波動）
  fast    較快 0.5×1M+0.5×3M

用法: python compare_rs_metric.py
"""

import sys
import warnings
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

from validate_strategy import DEFAULT_RISK
from compare_strategies import build_windows, run_strategy_set
from scanner_base import AI_TECH_STOCKS
from validate_universe import DIVERSE_UNIVERSE, download
from rs_selection import compute_rs_rank, make_rs_strat


def main():
    broad = sorted(set(AI_TECH_STOCKS) | set(DIVERSE_UNIVERSE))
    print(f"\n{'='*62}\n  RS 指標定義優化（廣股池 {len(broad)} 檔，RS前20% 固定）\n{'='*62}")
    bm, stocks = download(broad, 'SPY', '3y')
    if not stocks:
        print('❌ 下載失敗'); return
    windows, d0, d1 = build_windows(bm, 12, 6, 6)
    print(f"  資料 {d0}~{d1}, {len(windows)} 個 OOS 視窗  (基準 SPY)")

    modes = ['blend', 'mom126', 'mom252', 'ibd', '12m1m', 'voladj', 'fast']
    print("  預先計算各 RS 定義的排名...")
    ranks = {m: compute_rs_rank(stocks, mode=m) for m in modes}

    sums = []
    for m in modes:
        tag = ' (現行)' if m == 'blend' else ''
        sums.append(run_strategy_set(f'RS={m}{tag}',
                    lambda m=m: make_rs_strat(ranks[m], 80), stocks, bm, windows,
                    risk=DEFAULT_RISK))

    print(f"\n  📊 RS 指標定義對照 (廣股池, RS前20%, 3y 平均 OOS)")
    print(f"  {'RS 定義':<18} {'Sharpe':>7} {'PF':>6} {'WR':>5} {'MDD':>7} {'交易':>5}")
    print(f"  {'-'*52}")
    base = sums[0]
    for s in sums:
        d = '' if s is base else f'  Δ{s["sharpe"]-base["sharpe"]:+.2f}'
        print(f"  {s['name']:<18} {s['sharpe']:>+7.2f} {s['pf']:>6.2f} "
              f"{s['wr']:>4.0f}% {s['mdd']:>6.1f}% {s['trades']:>5}{d}")
    best = max(sums, key=lambda s: s['sharpe'])
    print(f"\n  🏆 最強 RS 定義: {best['name']} (Sharpe {best['sharpe']:+.2f})")
    print(f"{'='*62}\n")


if __name__ == '__main__':
    main()
