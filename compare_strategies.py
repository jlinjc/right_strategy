"""
compare_strategies.py - 三策略各自回測比較
================================================
目的：把 TD9 / MA拉回 / 動能突破 三個策略「拆開」各自跑 walk-forward OOS，
搞清楚到底是誰在賺錢，再決定主策略。

對照組：
  1. 只有 MAPullbackStrategy        (主策略候選)
  2. 只有 TD9BuyStrategy            (加分項候選)
  3. 只有 MomentumBreakoutStrategy  (動能突破)
  4. 三個混合 (現狀基準線)

每組都跑相同的 IS/OOS 滾動視窗，彙總 OOS 的 Sharpe / WR / PF / MDD / 交易筆數。

用法:
  python compare_strategies.py          # 2y 資料, fast 視窗 (IS=9 OOS=3 步進=3)
  python compare_strategies.py --3y     # 3y 資料, 標準視窗 (IS=12 OOS=6 步進=6)
"""

import sys
import argparse
from datetime import date

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import numpy as np

from validate_strategy import (
    _run_window, _safe, _add_months, download_data, DEFAULT_RISK,
)
from backtest_strategies import (
    TD9BuyStrategy, MAPullbackStrategy, MomentumBreakoutStrategy,
)


def build_windows(benchmark_df, is_m, oos_m, step_m):
    """建立 IS/OOS 滾動視窗列表"""
    if hasattr(benchmark_df.index[0], 'date'):
        all_dates = [d.date() for d in benchmark_df.index]
    else:
        all_dates = list(benchmark_df.index)
    data_start, data_end = all_dates[0], all_dates[-1]

    windows = []
    ws = data_start
    while True:
        is_end = _add_months(ws, is_m)
        oos_end = _add_months(is_end, oos_m)
        if oos_end > data_end:
            break
        windows.append((ws, is_end, oos_end))
        ws = _add_months(ws, step_m)
    return windows, data_start, data_end


def run_strategy_set(name, make_strats, stocks, bm, windows, risk=None):
    """對單一策略組合跑所有 OOS 視窗，彙總結果。risk=None 用 DEFAULT_RISK。"""
    risk = risk or DEFAULT_RISK
    print(f"\n{'='*60}")
    print(f"  ▶ {name}")
    print(f"{'='*60}")

    oos_rows = []
    for i, (ws, is_end, oos_end) in enumerate(windows):
        n = i + 1
        oos_r = _run_window(stocks, bm, is_end, oos_end,
                            make_strats(), risk=risk, label=f'W{n}_OOS')
        sh = _safe(oos_r, 'sharpe_ratio')
        pf = _safe(oos_r, 'profit_factor')
        wr = _safe(oos_r, 'win_rate_pct')
        mdd = _safe(oos_r, 'max_drawdown_pct', -99.0)
        nt = oos_r.get('_n_trades', 0) if 'error' not in oos_r else 0

        if 'error' in oos_r:
            print(f"  OOS {n} ({is_end}~{oos_end}): ⚠️ {oos_r['error']}")
        else:
            print(f"  OOS {n} ({is_end}~{oos_end}): "
                  f"Sharpe={sh:+.2f}  PF={pf:.2f}  WR={wr:.0f}%  "
                  f"MDD={mdd:.1f}%  交易={nt}筆")
        oos_rows.append({'sharpe': sh, 'pf': pf, 'wr': wr, 'mdd': mdd, 'trades': nt})

    # 只用有足夠交易筆數的視窗彙總
    valid = [r for r in oos_rows if r['trades'] >= 5]
    if not valid:
        print(f"  ❌ 無有效視窗 (交易筆數皆 < 5)")
        return {'name': name, 'valid': 0, 'sharpe': 0, 'pf': 0,
                'wr': 0, 'mdd': 0, 'trades': 0}

    summary = {
        'name': name,
        'valid': len(valid),
        'sharpe': float(np.mean([r['sharpe'] for r in valid])),
        'pf': float(np.mean([r['pf'] for r in valid])),
        'wr': float(np.mean([r['wr'] for r in valid])),
        'mdd': float(np.mean([r['mdd'] for r in valid])),
        'trades': int(sum(r['trades'] for r in valid)),
    }
    print(f"  ── 彙總 ({len(valid)}/{len(oos_rows)} 視窗有效) ──")
    print(f"  平均 OOS Sharpe={summary['sharpe']:+.2f}  PF={summary['pf']:.2f}  "
          f"WR={summary['wr']:.0f}%  MDD={summary['mdd']:.1f}%  總交易={summary['trades']}筆")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--3y', dest='three_y', action='store_true',
                        help='用 3y 資料 + 標準視窗')
    args = parser.parse_args()

    period = '3y' if args.three_y else '2y'
    is_m, oos_m, step_m = (12, 6, 6) if args.three_y else (9, 3, 3)

    print(f"\n{'='*60}")
    print(f"  三策略拆開回測比較  ({period} 資料)")
    print(f"{'='*60}")

    bm, stocks = download_data(period)
    if bm is None or not stocks:
        print('❌ 資料下載失敗，中止。')
        return

    windows, d0, d1 = build_windows(bm, is_m, oos_m, step_m)
    print(f"  視窗: IS={is_m}月 OOS={oos_m}月 步進={step_m}月 → {len(windows)} 個 OOS 視窗")
    print(f"  資料範圍: {d0} ~ {d1}")

    sets = [
        ('① MA 拉回 (純，無市場濾網)',
         lambda: [MAPullbackStrategy()]),
        ('② MA 拉回 + 市場濾網(QQQ>21EMA)',
         lambda: [MAPullbackStrategy(require_market_uptrend=True)]),
        ('③ 只有 TD9 逆向買入',
         lambda: [TD9BuyStrategy()]),
        ('④ 只有 動能突破',
         lambda: [MomentumBreakoutStrategy()]),
        ('⑤ 三策略混合 (現狀基準線)',
         lambda: [TD9BuyStrategy(), MAPullbackStrategy(), MomentumBreakoutStrategy()]),
    ]

    summaries = [run_strategy_set(name, mk, stocks, bm, windows) for name, mk in sets]

    # ── 最終對照表 ──
    print(f"\n{'='*60}")
    print(f"  📊 最終對照表 (平均 OOS 指標)")
    print(f"{'='*60}")
    print(f"  {'策略':<28} {'Sharpe':>7} {'PF':>6} {'WR':>5} {'MDD':>7} {'交易':>5}")
    print(f"  {'-'*58}")
    for s in summaries:
        print(f"  {s['name']:<28} {s['sharpe']:>+7.2f} {s['pf']:>6.2f} "
              f"{s['wr']:>4.0f}% {s['mdd']:>6.1f}% {s['trades']:>5}")
    print(f"{'='*60}\n")

    best = max(summaries, key=lambda s: s['sharpe'] if s['valid'] > 0 else -99)
    print(f"  🏆 OOS Sharpe 最高: {best['name']}  (Sharpe={best['sharpe']:+.2f})\n")


if __name__ == '__main__':
    main()
