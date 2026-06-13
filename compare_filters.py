"""
compare_filters.py - 進場濾網比較
=====================================
進場 MA 拉回 + 出場 Ⓗ(分批3ATR+吊燈3.5) 全固定，每次只加一個濾網，
跟無濾網基準比 OOS。找出真正有用的濾網。一次測一個（避免 curve-fit）。

用法:
  python compare_filters.py          # 2y 資料
  python compare_filters.py --3y     # 3y 資料
"""

import sys
import argparse

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from validate_strategy import download_data
from compare_strategies import build_windows, run_strategy_set
from filter_experiments import (
    FilteredScaledExit,
    f_vol_dryup, f_vol_surge, f_mom_positive,
    make_f_adx, make_f_rs_margin, make_f_not_extended,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--3y', dest='three_y', action='store_true')
    args = parser.parse_args()

    period = '3y' if args.three_y else '2y'
    is_m, oos_m, step_m = (12, 6, 6) if args.three_y else (9, 3, 3)

    print(f"\n{'='*60}")
    print(f"  進場濾網比較 (進場MA拉回 + 出場Ⓗ 固定)  {period} 資料")
    print(f"{'='*60}")

    bm, stocks = download_data(period)
    if bm is None or not stocks:
        print('❌ 資料下載失敗，中止。')
        return

    windows, d0, d1 = build_windows(bm, is_m, oos_m, step_m)
    print(f"  視窗: IS={is_m} OOS={oos_m} 步進={step_m}月 → {len(windows)} 個 OOS 視窗")
    print(f"  資料範圍: {d0} ~ {d1}")

    F_M = ('mom+', f_mom_positive)
    F_E = ('not_ext', make_f_not_extended(1.08))
    F_V1 = ('vol_dryup', f_vol_dryup)

    cfgs = [
        ('⓪ 無濾網 (基準=Ⓗ)',        lambda: [FilteredScaledExit(filters=[])]),
        ('Ｍ  TTM動能為正',           lambda: [FilteredScaledExit(filters=[F_M])]),
        ('Ｅ  不追高 (<1.08×10MA)',    lambda: [FilteredScaledExit(filters=[F_E])]),
        ('Ｍ+Ｅ 動能+不追高',         lambda: [FilteredScaledExit(filters=[F_M, F_E])]),
        ('Ｍ+Ｅ+Ｖ1 再加量縮',        lambda: [FilteredScaledExit(filters=[F_M, F_E, F_V1])]),
    ]

    summaries = [run_strategy_set(name, mk, stocks, bm, windows)
                 for name, mk in cfgs]

    print(f"\n{'='*60}")
    print(f"  📊 濾網對照表 (平均 OOS 指標)")
    print(f"{'='*60}")
    print(f"  {'濾網':<24} {'Sharpe':>7} {'PF':>6} {'WR':>5} {'MDD':>7} {'交易':>5}")
    print(f"  {'-'*58}")
    base = summaries[0]
    for s in summaries:
        d_sh = s['sharpe'] - base['sharpe']
        mark = '' if s is base else (f'  Δ{d_sh:+.2f}')
        print(f"  {s['name']:<24} {s['sharpe']:>+7.2f} {s['pf']:>6.2f} "
              f"{s['wr']:>4.0f}% {s['mdd']:>6.1f}% {s['trades']:>5}{mark}")
    print(f"{'='*60}")
    print(f"  (Δ = 相對無濾網基準的 Sharpe 變化；交易數大幅下降代表濾網太嚴)\n")


if __name__ == '__main__':
    main()
