"""
compare_concentration.py - 集中度 / 相關性風控比較（洞#4）
=============================================================
真實 MDD 來自「同時持有高度相關的科技股」。測能否在不殺 Sharpe 下壓低真實回撤：
  - 降最大持倉數 max_positions
  - 縮緊單板塊上限 max_sector_positions
  - 組合總風險上限 portfolio heat（最精準的相關曝險閘門）

進場+出場固定定案系統，跑 3y(誠實窗口)。重點看 MDD 與「Sharpe/|MDD|」效率。

用法: python compare_concentration.py
"""

import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from validate_strategy import download_data, DEFAULT_RISK
from compare_strategies import build_windows, run_strategy_set
from filter_experiments import FilteredScaledExit, f_mom_positive, make_f_not_extended


def final_strat():
    return [FilteredScaledExit(
        filters=[('mom+', f_mom_positive), ('not_ext', make_f_not_extended(1.08))],
        atr_target_mult=3.0, scale_frac=0.5, trail_mult=3.5)]


def risk(**over):
    return {**DEFAULT_RISK, **over}


def main():
    print(f"\n{'='*62}\n  集中度/相關性風控比較（定案系統，3y 誠實窗口）\n{'='*62}")
    bm, stocks = download_data('3y')
    if bm is None or not stocks:
        print('❌ 下載失敗'); return
    windows, d0, d1 = build_windows(bm, 12, 6, 6)
    print(f"  資料 {d0}~{d1}, {len(windows)} 個 OOS 視窗")

    cfgs = [
        ('⓪ 基準 (6倉/板塊3/無heat)', risk()),
        ('Ｐ5 max_positions=5',       risk(max_positions=5)),
        ('Ｐ4 max_positions=4',       risk(max_positions=4)),
        ('Ｐ3 max_positions=3',       risk(max_positions=3)),
        ('Ｓ2 單板塊上限=2',          risk(max_sector_positions=2)),
        ('Ｈ5 組合heat上限5%',        risk(max_portfolio_heat=0.05)),
        ('Ｈ4 組合heat上限4%',        risk(max_portfolio_heat=0.04)),
        ('Ｈ3 組合heat上限3%',        risk(max_portfolio_heat=0.03)),
        ('Ｃ 組合 P4+板塊2+heat4%',   risk(max_positions=4, max_sector_positions=2, max_portfolio_heat=0.04)),
    ]
    sums = []
    for name, rk in cfgs:
        sums.append(run_strategy_set(name, final_strat, stocks, bm, windows, risk=rk))

    print(f"\n  📊 集中度對照 (3y 平均 OOS)")
    print(f"  {'配置':<26} {'Sharpe':>7} {'PF':>6} {'WR':>5} {'MDD':>7} {'效率':>6} {'交易':>5}")
    print(f"  {'-'*64}")
    base = sums[0]
    for s in sums:
        eff = s['sharpe'] / abs(s['mdd']) * 100 if s['mdd'] else 0   # Sharpe per 1% MDD ×100
        dm = '' if s is base else f'  ΔMDD{s["mdd"]-base["mdd"]:+.1f}'
        print(f"  {s['name']:<26} {s['sharpe']:>+7.2f} {s['pf']:>6.2f} "
              f"{s['wr']:>4.0f}% {s['mdd']:>6.1f}% {eff:>6.2f} {s['trades']:>5}{dm}")
    print(f"  (效率 = Sharpe / |MDD| ×100，越高代表每單位回撤換到越多風報)")
    print(f"{'='*62}\n")


if __name__ == '__main__':
    main()
