"""
compare_rs.py - 橫斷面 RS 排名選股驗證（完成策略的關鍵一步）
================================================================
在「廣股池(AI 50 ∪ 中性 62，去重 ~100+ 檔，跨全板塊)」上，測 RS 排名選股能否
把 edge 系統化救回來。對照：
  - 無 RS 排名（等同把策略套整個廣股池）
  - RS 前 30% / 20% / 10%

若前 X% 明顯優於無排名，代表「交易領導者」可用規則重現 → edge 真可重複、非主題紅利。
基準用 SPY。3y 誠實窗口。

用法: python compare_rs.py
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
from filter_experiments import FilteredScaledExit, f_mom_positive, make_f_not_extended


def no_rank_strat():
    return [FilteredScaledExit(
        filters=[('mom+', f_mom_positive), ('not_ext', make_f_not_extended(1.08))],
        atr_target_mult=3.0, scale_frac=0.5, trail_mult=3.5)]


def main():
    # 廣股池 = AI 50 ∪ 中性 62，去重
    broad = sorted(set(AI_TECH_STOCKS) | set(DIVERSE_UNIVERSE))
    print(f"\n{'='*62}\n  RS 排名選股驗證（廣股池 {len(broad)} 檔，跨全板塊）\n{'='*62}")

    bm, stocks = download(broad, 'SPY', '3y')
    if not stocks:
        print('❌ 下載失敗'); return

    print("  計算每日橫斷面 RS 排名(多週期動能)...")
    rs_rank = compute_rs_rank(stocks)

    windows, d0, d1 = build_windows(bm, 12, 6, 6)
    print(f"  資料 {d0}~{d1}, {len(windows)} 個 OOS 視窗  (基準 SPY)\n")

    cfgs = [
        ('無 RS 排名 (整個廣股池)', no_rank_strat),
        ('RS 前 30% (≥70)',        lambda: make_rs_strat(rs_rank, 70)),
        ('RS 前 20% (≥80)',        lambda: make_rs_strat(rs_rank, 80)),
        ('RS 前 10% (≥90)',        lambda: make_rs_strat(rs_rank, 90)),
    ]
    sums = [run_strategy_set(n, mk, stocks, bm, windows, risk=DEFAULT_RISK) for n, mk in cfgs]

    print(f"\n  📊 RS 排名選股結果 (廣股池, 3y 平均 OOS)")
    print(f"  {'配置':<24} {'Sharpe':>7} {'PF':>6} {'WR':>5} {'MDD':>7} {'交易':>5}")
    print(f"  {'-'*58}")
    base = sums[0]
    for s in sums:
        d = '' if s is base else f'  Δ{s["sharpe"]-base["sharpe"]:+.2f}'
        print(f"  {s['name']:<24} {s['sharpe']:>+7.2f} {s['pf']:>6.2f} "
              f"{s['wr']:>4.0f}% {s['mdd']:>6.1f}% {s['trades']:>5}{d}")
    print(f"\n  對照: 原手挑 AI 股池 3y ≈ +1.9~2.2  |  整個中性股池(無排名) ≈ +0.05")
    best = max(sums[1:], key=lambda s: s['sharpe'])
    verdict = ('✅ RS 選股系統化地救回 edge → 策略可重複、非事後諸葛'
               if best['sharpe'] > 1.0 else
               ('⚠️ RS 選股有幫助但未完全救回 edge' if best['sharpe'] > 0.5 else
                '❌ RS 選股仍救不回 → edge 高度依賴特定主題'))
    print(f"  最佳 RS 門檻: {best['name']} (Sharpe {best['sharpe']:+.2f})")
    print(f"  → 判讀: {verdict}")
    print(f"{'='*62}\n")


if __name__ == '__main__':
    main()
