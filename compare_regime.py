"""
compare_regime.py - 系統化大盤 REGIME 濾網比較
=================================================
在定案系統(進場MA拉回+Ｍ+Ｅ / 出場Ⓗ)之上，疊加不同的「大盤 regime gate」，
測能否系統化地修掉修正期失血、改善 3y MDD 與 Sharpe。
重點看 3y(含空頭盤整)；2y 一起跑做對照。

用法:
  python compare_regime.py          # 跑 2y 與 3y 兩個窗口
"""

import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from validate_strategy import download_data
from compare_strategies import build_windows, run_strategy_set
from filter_experiments import (
    FilteredScaledExit, f_mom_positive, make_f_not_extended,
    make_f_index_above_ma, make_f_index_golden, make_f_index_rising,
)

# 定案核心濾網（個股層級）
F_CORE = [('mom+', f_mom_positive), ('not_ext', make_f_not_extended(1.08))]


def make_strat(extra=None):
    fl = list(F_CORE) + (extra or [])
    return [FilteredScaledExit(filters=fl, atr_target_mult=3.0,
                               scale_frac=0.5, trail_mult=3.5)]


def run_period(period, is_m, oos_m, step_m):
    print(f"\n{'#'*60}\n#  視窗 {period}  (IS={is_m} OOS={oos_m} 步進={step_m})\n{'#'*60}")
    bm, stocks = download_data(period)
    if bm is None or not stocks:
        print('❌ 下載失敗'); return
    windows, d0, d1 = build_windows(bm, is_m, oos_m, step_m)
    print(f"  資料 {d0}~{d1}, {len(windows)} 個 OOS 視窗")

    cfgs = [
        ('⓪ 無 regime gate (定案基準)', lambda: make_strat()),
        ('Ｇ1 QQQ>200MA',             lambda: make_strat([('idx200', make_f_index_above_ma(200))])),
        ('Ｇ2 QQQ>50MA',              lambda: make_strat([('idx50', make_f_index_above_ma(50))])),
        ('Ｇ3 QQQ 50>200 金叉',       lambda: make_strat([('golden', make_f_index_golden(50, 200))])),
        ('Ｇ4 QQQ 50MA上升中',        lambda: make_strat([('rising', make_f_index_rising(50, 10))])),
        ('Ｇ5 200MA且50上升',         lambda: make_strat([('idx200', make_f_index_above_ma(200)),
                                                          ('rising', make_f_index_rising(50, 10))])),
    ]
    summaries = [run_strategy_set(n, mk, stocks, bm, windows) for n, mk in cfgs]

    print(f"\n  📊 {period} REGIME 對照 (平均 OOS)")
    print(f"  {'配置':<26} {'Sharpe':>7} {'PF':>6} {'WR':>5} {'MDD':>7} {'交易':>5}")
    base = summaries[0]
    for s in summaries:
        d = '' if s is base else f'  Δ{s["sharpe"]-base["sharpe"]:+.2f}'
        print(f"  {s['name']:<26} {s['sharpe']:>+7.2f} {s['pf']:>6.2f} "
              f"{s['wr']:>4.0f}% {s['mdd']:>6.1f}% {s['trades']:>5}{d}")


def main():
    run_period('3y', 12, 6, 6)   # 誠實窗口（含修正期）— 決定性
    run_period('2y', 9, 3, 3)    # 對照


if __name__ == '__main__':
    main()
