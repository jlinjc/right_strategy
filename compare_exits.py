"""
compare_exits.py - 出場策略比較（進場固定 MA 拉回）
======================================================
同一個 MA 拉回進場，替換不同出場邏輯，跑相同 walk-forward OOS 視窗，
找出最適合這套右側動能的出場法。用數據說話。

用法:
  python compare_exits.py          # 2y 資料, fast 視窗 (IS=9 OOS=3 步進=3)
  python compare_exits.py --3y     # 3y 資料, 標準視窗 (IS=12 OOS=6 步進=6)
"""

import sys
import argparse

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import numpy as np

from validate_strategy import _run_window, _safe, download_data, DEFAULT_RISK
from compare_strategies import build_windows, run_strategy_set
from exit_experiments import (
    ExitBaseline, ExitMACross, ExitChandelier,
    ExitFibExtension, ExitAdamMirror, ExitTimeStop, ExitScaledHybrid,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--3y', dest='three_y', action='store_true')
    args = parser.parse_args()

    period = '3y' if args.three_y else '2y'
    is_m, oos_m, step_m = (12, 6, 6) if args.three_y else (9, 3, 3)

    print(f"\n{'='*60}")
    print(f"  出場策略比較 (進場固定 MA 拉回)  {period} 資料")
    print(f"{'='*60}")

    bm, stocks = download_data(period)
    if bm is None or not stocks:
        print('❌ 資料下載失敗，中止。')
        return

    windows, d0, d1 = build_windows(bm, is_m, oos_m, step_m)
    print(f"  視窗: IS={is_m} OOS={oos_m} 步進={step_m}月 → {len(windows)} 個 OOS 視窗")
    print(f"  資料範圍: {d0} ~ {d1}")

    exits = [
        ('① 基準線 (現有出場)',          lambda: [ExitBaseline()]),
        ('④ 吊燈 (3×ATR)',              lambda: [ExitChandelier(mult=3.0)]),
        ('Ⓒ 分批 Fib1.272/50%/吊燈3.5', lambda: [ExitScaledHybrid(fib_ratio=1.272, scale_frac=0.5, trail_mult=3.5)]),
        # ── 第一段目標改用 ATR（更近、更常觸發分批，測 WR 能否再升） ──
        ('Ⓔ 分批 1.5ATR/50%/吊燈3.5',   lambda: [ExitScaledHybrid(atr_target_mult=1.5, scale_frac=0.5, trail_mult=3.5)]),
        ('Ⓕ 分批 2.0ATR/50%/吊燈3.5',   lambda: [ExitScaledHybrid(atr_target_mult=2.0, scale_frac=0.5, trail_mult=3.5)]),
        ('Ⓖ 分批 2.5ATR/50%/吊燈3.5',   lambda: [ExitScaledHybrid(atr_target_mult=2.5, scale_frac=0.5, trail_mult=3.5)]),
        ('Ⓗ 分批 3.0ATR/50%/吊燈3.5',   lambda: [ExitScaledHybrid(atr_target_mult=3.0, scale_frac=0.5, trail_mult=3.5)]),
    ]

    summaries = [run_strategy_set(name, mk, stocks, bm, windows)
                 for name, mk in exits]

    print(f"\n{'='*60}")
    print(f"  📊 出場策略對照表 (平均 OOS 指標)")
    print(f"{'='*60}")
    print(f"  {'出場策略':<26} {'Sharpe':>7} {'PF':>6} {'WR':>5} {'MDD':>7} {'交易':>5}")
    print(f"  {'-'*58}")
    for s in summaries:
        print(f"  {s['name']:<26} {s['sharpe']:>+7.2f} {s['pf']:>6.2f} "
              f"{s['wr']:>4.0f}% {s['mdd']:>6.1f}% {s['trades']:>5}")
    print(f"{'='*60}")

    best_sh = max(summaries, key=lambda s: s['sharpe'] if s['valid'] > 0 else -99)
    best_pf = max(summaries, key=lambda s: s['pf'] if s['valid'] > 0 else -99)
    print(f"\n  🏆 OOS Sharpe 最高: {best_sh['name']}  (Sharpe={best_sh['sharpe']:+.2f})")
    print(f"  💰 OOS PF 最高:     {best_pf['name']}  (PF={best_pf['pf']:.2f})\n")


if __name__ == '__main__':
    main()
