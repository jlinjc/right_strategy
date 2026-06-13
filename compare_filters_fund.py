"""
compare_filters_fund.py - 基本面濾網方向性研究（洞#2）
=========================================================
⚠️ look-ahead 污染：用「當下」基本面套到過去回測，今天成長/便宜的股票事後本來
就會漲，所以「成長/估值」濾網會被高估。本研究只能：
  (1) 排除無效因子（即使有利偏差還是沒幫助 → 真沒用）
  (2) 看「品質類」(margin/ROE，結構穩定污染最小)是否仍加分
真正驗證要靠 paper trading 前瞻。

進場 RS前20%(voladj) + 定案進出場 固定，廣股池 109 檔，3y SPY。

用法: python compare_filters_fund.py
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
from rs_selection import compute_rs_rank
from filter_experiments import f_mom_positive, make_f_not_extended
from fundamentals_filter import (
    fetch_fundamentals, RSFundScaledExit,
    make_f_margin, make_f_roe, make_f_rev_growth, make_f_earn_growth,
    make_f_not_expensive, make_f_analyst_buy,
)

CORE = [('mom+', f_mom_positive), ('not_ext', make_f_not_extended(1.08))]


def main():
    broad = sorted(set(AI_TECH_STOCKS) | set(DIVERSE_UNIVERSE))
    print(f"\n{'='*62}\n  基本面濾網方向性研究 ⚠️污染 (廣股池 {len(broad)}, RS前20% voladj)\n{'='*62}")
    bm, stocks = download(broad, 'SPY', '3y')
    if not stocks:
        print('❌ 下載失敗'); return
    rs = compute_rs_rank(stocks, mode='voladj')
    fund = fetch_fundamentals(list(stocks.keys()))
    windows, d0, d1 = build_windows(bm, 12, 6, 6)
    print(f"  資料 {d0}~{d1}, {len(windows)} 個 OOS 視窗\n")

    def mk(fund_filters):
        return [RSFundScaledExit(
            rs_rank=rs, rs_threshold=80, filters=CORE, fund_filters=fund_filters,
            atr_target_mult=3.0, scale_frac=0.5, trail_mult=3.5)]

    cfgs = [
        ('⓪ 無基本面濾網(基準)',     lambda: mk([])),
        ('品質: 淨利率>10%',         lambda: mk([make_f_margin(fund, 0.10)])),
        ('品質: ROE>15%',           lambda: mk([make_f_roe(fund, 0.15)])),
        ('成長: 營收年增>15%',       lambda: mk([make_f_rev_growth(fund, 0.15)])),
        ('成長: 盈餘年增>0',         lambda: mk([make_f_earn_growth(fund, 0.0)])),
        ('估值: 前瞻PE<60',          lambda: mk([make_f_not_expensive(fund, 60)])),
        ('分析師: buy以上',          lambda: mk([make_f_analyst_buy(fund)])),
        ('品質組合: 淨利率10%+ROE15%', lambda: mk([make_f_margin(fund,0.10), make_f_roe(fund,0.15)])),
    ]
    sums = [run_strategy_set(n, f, stocks, bm, windows, risk=DEFAULT_RISK) for n, f in cfgs]

    print(f"\n  📊 基本面濾網方向性 (污染! 3y 平均 OOS)")
    print(f"  {'濾網':<26} {'Sharpe':>7} {'PF':>6} {'WR':>5} {'MDD':>7} {'交易':>5}")
    print(f"  {'-'*60}")
    base = sums[0]
    for s in sums:
        d = '' if s is base else f'  Δ{s["sharpe"]-base["sharpe"]:+.2f}'
        print(f"  {s['name']:<26} {s['sharpe']:>+7.2f} {s['pf']:>6.2f} "
              f"{s['wr']:>4.0f}% {s['mdd']:>6.1f}% {s['trades']:>5}{d}")
    print(f"\n  ⚠️ 成長/估值濾網的Δ受 look-ahead 高估；品質類(margin/ROE)較可信。")
    print(f"  判讀重點：哪些『即使有利偏差仍沒幫助』→ 真沒用該排除。")
    print(f"{'='*62}\n")


if __name__ == '__main__':
    main()
