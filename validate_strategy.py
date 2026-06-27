"""
validate_strategy.py - Anti-Gravity 策略驗證引擎
===================================================
兩層驗證 + 最終裁決:

  層次 1: Walk-forward 樣本外驗證
          IS=12月 / OOS=6月 / 步進=6月 → 4 個 OOS 視窗
          驗證 edge 是真實的，不是 curve-fit

  層次 2: 參數敏感度掃描
          逐一變動各關鍵參數，其餘固定預設
          驗證參數是穩健的，不是魔術數字

  最終裁決: PASS / CONDITIONAL / NEEDS WORK / FAIL

用法:
  python validate_strategy.py              # 完整驗證 (~5-15 分鐘)
  python validate_strategy.py --fast       # 快速模式 (2y 資料 + 精簡敏感度)
  python validate_strategy.py --wf-only   # 只跑 walk-forward
  python validate_strategy.py --sens-only  # 只跑敏感度掃描

裁決門檻:
  ✅ PASS:         OOS Sharpe ≥ 0.7 AND 保留度 ≥ 50% AND OOS PF ≥ 1.2
                   AND OOS MDD ≥ -25% AND 無脆弱參數
  ⚠️ CONDITIONAL:  僅 1 項未達標
  ⚠️ NEEDS WORK:   2-3 項未達標
  ❌ FAIL:         ≥ 4 項未達標 (嚴重 curve-fit 或無 edge)
"""

import os
import sys
import json
import warnings
import argparse
import calendar
from datetime import datetime, date
from typing import List, Dict, Optional

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import yfinance as yf

from scanner_base import AI_TECH_STOCKS, BENCHMARK, DASHBOARD_DIR
from backtest_engine import Portfolio, RiskManager, PerformanceAnalyzer
from backtest_strategies import (
    TD9BuyStrategy, MAPullbackStrategy, MomentumBreakoutStrategy,
)


# ============================================================
# 預設參數與門檻
# ============================================================
DEFAULT_RISK = dict(
    initial_capital=100_000,
    risk_per_trade=0.01,
    max_positions=6,
    max_daily_loss=0.03,
    max_sector_positions=3,
    max_portfolio_heat=0.03,   # 組合總風險上限 3%（洞#4：壓低相關曝險的真實回撤）
    commission=1.0,
    slippage=0.0005,
)

# Phase 0（誠實的尺）：停損成交模型
#   True  = 跳空開盤低於停損時，以「開盤價」成交(更差) → 如實反映崩盤左尾
#   False = 舊樂觀假設：永遠在停損價本身成交(系統性低估崩盤虧損/MDD)
# 預設 True（誠實）。設 False 可重現舊回測、量化「跳空樂觀」灌了多少水。
HONEST_STOP_FILL = True

PASS_THR = dict(
    min_sharpe_oos=0.7,           # OOS Sharpe 最低門檻
    min_is_oos_retention=0.50,    # OOS Sharpe 需保留 IS Sharpe 的 50%
    min_pf_oos=1.2,               # OOS Profit Factor
    min_trades_oos=8,             # OOS 最少交易筆數 (不足則視窗無效)
    max_mdd_oos=-25.0,            # OOS 最大回撤上限
    fragility_threshold=0.50,     # 鄰近參數差距 > 此比例 → 標記脆弱
)


# ============================================================
# 工具函式
# ============================================================
def _add_months(d: date, months: int) -> date:
    """日期 + N 個月，自動處理月底日期"""
    m = d.month + months
    y = d.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def _find_bar(df: pd.DataFrame, target: date):
    """在 DataFrame.index 中找到指定日期，回傳 index 值或 None"""
    if hasattr(df.index[0], 'date'):
        matches = df.index[df.index.date == target]
    else:
        matches = df.index[df.index == target]
    return matches[0] if len(matches) > 0 else None


def _safe(r: dict, key: str, default=0.0):
    if 'error' in r:
        return default
    return r.get('summary', {}).get(key, default)


# ============================================================
# 核心: 單次日期視窗回測
# ============================================================
def _run_window(
    stocks_data: dict,
    benchmark_df: pd.DataFrame,
    start: date,
    end: date,
    strategies: list,
    risk: dict = None,
    label: str = '',
) -> dict:
    """
    在 [start, end] 日期區間跑完整回測。
    strategies 是已實例化的策略物件列表。
    回傳 PerformanceAnalyzer.analyze() 結果，或含 'error' 的 dict。
    """
    rp = risk or DEFAULT_RISK

    portfolio = Portfolio(
        initial_capital=rp['initial_capital'],
        commission_per_trade=rp['commission'],
        slippage_pct=rp['slippage'],
    )
    risk_mgr = RiskManager(
        max_risk_per_trade=rp['risk_per_trade'],
        max_positions=rp['max_positions'],
        max_daily_loss_pct=rp['max_daily_loss'],
        max_sector_positions=rp['max_sector_positions'],
        max_portfolio_heat=rp.get('max_portfolio_heat'),
    )
    strat_map = {s.name: s for s in strategies}

    # 篩出視窗內的交易日
    if hasattr(benchmark_df.index[0], 'date'):
        window_dates = [d for d in benchmark_df.index if start <= d.date() <= end]
    else:
        window_dates = [d for d in benchmark_df.index if start <= d <= end]

    if len(window_dates) < 30:
        return {'error': f'[{label}] 視窗交易日不足 ({len(window_dates)} 天，需 ≥ 30)'}

    pending_signals = []

    for current_ts in window_dates:
        cd = current_ts.date() if hasattr(current_ts, 'date') else current_ts

        # ── 執行前日訊號 ──────────────────────────────
        for sig in pending_signals:
            if sig.ticker not in stocks_data:
                continue
            ik = _find_bar(stocks_data[sig.ticker], cd)
            if ik is None:
                continue
            bi = stocks_data[sig.ticker].index.get_loc(ik)
            o = float(stocks_data[sig.ticker]['Open'].iloc[bi])
            if pd.isna(o) or o <= 0:
                continue
            ok, _ = risk_mgr.can_trade(portfolio, sig)
            if not ok:
                continue
            sh = risk_mgr.calculate_position_size(portfolio, o, sig.stop_loss)
            if sh > 0:
                portfolio.open_position(sig, sh, o)
        pending_signals.clear()

        # ── 收集今日 OHLCV ────────────────────────────
        prices: Dict[str, dict] = {}
        for tk, tdf in stocks_data.items():
            ik = _find_bar(tdf, cd)
            if ik is None:
                continue
            bi = tdf.index.get_loc(ik)
            prices[tk] = {col: float(tdf[col].iloc[bi])
                          for col in ('Open', 'High', 'Low', 'Close', 'Volume')}

        # ── 出場檢查 ──────────────────────────────────
        for tk in list(portfolio.positions.keys()):
            pos = portfolio.positions[tk]
            strat = strat_map.get(pos.strategy)
            if not strat or tk not in stocks_data:
                continue
            ik = _find_bar(stocks_data[tk], cd)
            if ik is None:
                continue
            bi = stocks_data[tk].index.get_loc(ik)
            res = strat.check_exit(pos, bi, stocks_data[tk])
            if res:
                # 分批出場訊號: ('scale', fraction, fill_price, reason)
                if isinstance(res, tuple) and res and res[0] == 'scale':
                    _, frac, fill_px, reason = res
                    portfolio.scale_out(tk, frac, fill_px, cd, reason)
                else:
                    reason = res
                    ep = prices.get(tk, {}).get('Close', pos.stop_loss)
                    if '停損' in reason and prices.get(tk, {}).get('Low', ep) <= pos.stop_loss:
                        if HONEST_STOP_FILL:
                            # 跳空開盤已在停損下方 → 以開盤成交(更差)；否則停損價內成交
                            op = prices.get(tk, {}).get('Open', pos.stop_loss)
                            ep = min(op, pos.stop_loss)
                        else:
                            ep = pos.stop_loss   # 舊樂觀假設：不跳空
                    portfolio.close_position(tk, ep, cd, reason)

        portfolio.update_daily(cd, prices)

        # ── 掃描新訊號 ───────────────────────────────
        new_sigs = []
        for tk, tdf in stocks_data.items():
            ik = _find_bar(tdf, cd)
            if ik is None:
                continue
            bi = tdf.index.get_loc(ik)
            for strat in strategies:
                sig = strat.scan(bi, tk, tdf, benchmark_df)
                if sig:
                    new_sigs.append(sig)
        new_sigs.sort(key=lambda s: s.priority, reverse=True)
        pending_signals = new_sigs

    # ── 強制平倉 ──────────────────────────────────────
    last_ts = window_dates[-1]
    last_cd = last_ts.date() if hasattr(last_ts, 'date') else last_ts
    final_p = {}
    for tk, tdf in stocks_data.items():
        ik = _find_bar(tdf, last_cd)
        if ik:
            bi = tdf.index.get_loc(ik)
            final_p[tk] = {col: float(tdf[col].iloc[bi])
                           for col in ('Open', 'High', 'Low', 'Close')}
    portfolio.force_close_all(last_cd, final_p)

    if not portfolio.closed_trades:
        return {'error': f'[{label}] 無任何成交交易'}

    result = PerformanceAnalyzer(portfolio).analyze()
    result['_label'] = label
    result['_range'] = f"{start} ~ {end}"
    result['_n_trades'] = len(portfolio.closed_trades)
    # 逐筆損益（給 Monte Carlo / 穩健性分析用）
    result['_trades'] = [
        {'pnl': t.pnl, 'pnl_pct': t.pnl_pct, 'hold_days': t.hold_days,
         'win': bool(t.is_win)}
        for t in portfolio.closed_trades
    ]
    result['_final_equity'] = portfolio.equity_curve[-1]['equity'] if portfolio.equity_curve else None
    return result


# ============================================================
# 層次 1: Walk-forward 樣本外驗證
# ============================================================
def run_walk_forward(stocks_data: dict, benchmark_df: pd.DataFrame,
                     fast: bool = False) -> dict:
    """
    滾動 IS/OOS 視窗。
    fast=False: IS=12月 OOS=6月 步進=6月 → 需約 3 年資料
    fast=True:  IS=9月  OOS=3月 步進=3月 → 需約 2 年資料
    """
    print(f"\n{'='*65}")
    print(f"  層次 1 — Walk-forward 樣本外驗證")
    print(f"{'='*65}")

    # 主策略確定為純 MA 拉回（compare_strategies.py 證實 TD9/動能突破拖累 Sharpe）
    default_strategies = [MAPullbackStrategy()]

    is_m, oos_m, step_m = (9, 3, 3) if fast else (12, 6, 6)

    if hasattr(benchmark_df.index[0], 'date'):
        all_dates = [d.date() for d in benchmark_df.index]
    else:
        all_dates = list(benchmark_df.index)

    data_start, data_end = all_dates[0], all_dates[-1]
    total_m = (data_end.year - data_start.year) * 12 + (data_end.month - data_start.month)
    print(f"  資料: {data_start} ~ {data_end}  ({total_m} 個月)")
    print(f"  IS={is_m}月 / OOS={oos_m}月 / 步進={step_m}月\n")

    # 建立視窗列表
    windows = []
    ws = data_start
    while True:
        is_end = _add_months(ws, is_m)
        oos_end = _add_months(is_end, oos_m)
        if oos_end > data_end:
            break
        windows.append((ws, is_end, oos_end))
        ws = _add_months(ws, step_m)

    if not windows:
        return {'error': '資料不足以建立 IS/OOS 視窗', 'windows': []}

    print(f"  共 {len(windows)} 個視窗\n")

    results = []
    for i, (ws, is_end, oos_end) in enumerate(windows):
        n = i + 1
        print(f"  視窗 {n}/{len(windows)}  IS:{ws}~{is_end}  OOS:{is_end}~{oos_end}")

        is_r = _run_window(stocks_data, benchmark_df, ws, is_end,
                           default_strategies, label=f'W{n}_IS')
        oos_r = _run_window(stocks_data, benchmark_df, is_end, oos_end,
                            default_strategies, label=f'W{n}_OOS')

        is_sh = _safe(is_r, 'sharpe_ratio')
        oos_sh = _safe(oos_r, 'sharpe_ratio')
        oos_pf = _safe(oos_r, 'profit_factor')
        oos_wr = _safe(oos_r, 'win_rate_pct')
        oos_mdd = _safe(oos_r, 'max_drawdown_pct', -99.0)
        oos_nt = oos_r.get('_n_trades', 0) if 'error' not in oos_r else 0
        is_nt = is_r.get('_n_trades', 0) if 'error' not in is_r else 0

        retention = (oos_sh / is_sh) if is_sh > 0.01 else 0.0

        w_pass = (
            oos_nt >= PASS_THR['min_trades_oos'] and
            oos_sh >= PASS_THR['min_sharpe_oos'] and
            retention >= PASS_THR['min_is_oos_retention']
        )
        status = '✅' if w_pass else ('⚠️' if oos_sh >= 0.3 else '❌')

        is_err = is_r.get('error', '')
        oos_err = oos_r.get('error', '')

        if is_err:
            print(f"    IS  → ⚠️ {is_err}")
        else:
            print(f"    IS  → Sharpe={is_sh:+.2f}  PF={_safe(is_r,'profit_factor'):.2f}"
                  f"  WR={_safe(is_r,'win_rate_pct'):.0f}%  交易={is_nt}筆"
                  f"  MDD={_safe(is_r,'max_drawdown_pct',-99):.1f}%")

        if oos_err:
            print(f"    OOS → ⚠️ {oos_err}")
        else:
            print(f"    OOS → Sharpe={oos_sh:+.2f}  PF={oos_pf:.2f}"
                  f"  WR={oos_wr:.0f}%  交易={oos_nt}筆"
                  f"  MDD={oos_mdd:.1f}%")

        print(f"    {status} 保留度={retention*100:.0f}%\n")

        results.append({
            'window': n,
            'is_range': f"{ws} ~ {is_end}",
            'oos_range': f"{is_end} ~ {oos_end}",
            'is':  {'sharpe': round(is_sh, 3),  'pf': round(_safe(is_r,'profit_factor'), 3),
                    'wr': round(_safe(is_r,'win_rate_pct'), 1), 'trades': is_nt,
                    'mdd': round(_safe(is_r,'max_drawdown_pct', -99), 2)},
            'oos': {'sharpe': round(oos_sh, 3), 'pf': round(oos_pf, 3),
                    'wr': round(oos_wr, 1), 'trades': oos_nt,
                    'mdd': round(oos_mdd, 2)},
            'retention': round(retention, 3),
            'pass': w_pass,
        })

    # ── 彙總 ──────────────────────────────────────────
    valid = [w for w in results if w['oos']['trades'] >= PASS_THR['min_trades_oos']]
    if not valid:
        print("  ❌ 所有 OOS 視窗交易筆數不足（無法評估）")
        return {'error': '所有視窗交易不足', 'windows': results}

    avg_sh = float(np.mean([w['oos']['sharpe'] for w in valid]))
    avg_ret = float(np.mean([w['retention'] for w in valid]))
    avg_pf = float(np.mean([w['oos']['pf'] for w in valid]))
    avg_mdd = float(np.mean([w['oos']['mdd'] for w in valid]))
    pass_cnt = sum(1 for w in valid if w['pass'])

    overall = (
        avg_sh  >= PASS_THR['min_sharpe_oos'] and
        avg_ret >= PASS_THR['min_is_oos_retention'] and
        avg_pf  >= PASS_THR['min_pf_oos'] and
        avg_mdd >= PASS_THR['max_mdd_oos']
    )

    print(f"  {'─'*55}")
    print(f"  彙總 ({len(valid)}/{len(results)} 視窗有效)")
    print(f"  平均 OOS Sharpe  : {avg_sh:+.2f}  (門檻 ≥{PASS_THR['min_sharpe_oos']:.1f})")
    print(f"  平均保留度       : {avg_ret*100:.0f}%  (門檻 ≥{PASS_THR['min_is_oos_retention']*100:.0f}%)")
    print(f"  平均 OOS PF      : {avg_pf:.2f}  (門檻 ≥{PASS_THR['min_pf_oos']:.1f})")
    print(f"  平均 OOS MDD     : {avg_mdd:.1f}%  (門檻 ≥{PASS_THR['max_mdd_oos']:.0f}%)")
    print(f"  通過視窗         : {pass_cnt}/{len(valid)}")
    print(f"  → Walk-forward: {'✅ PASS' if overall else '❌ FAIL'}\n")

    return {
        'overall_pass': overall,
        'avg_oos_sharpe': round(avg_sh, 3),
        'avg_retention': round(avg_ret, 3),
        'avg_oos_pf': round(avg_pf, 3),
        'avg_oos_mdd': round(avg_mdd, 2),
        'pass_count': pass_cnt,
        'total_valid': len(valid),
        'windows': results,
    }


# ============================================================
# 層次 2: 參數敏感度掃描
# ============================================================
def run_sensitivity_scan(stocks_data: dict, benchmark_df: pd.DataFrame,
                         fast: bool = False) -> dict:
    """
    逐一變動各關鍵參數，其餘固定預設值，評估 Sharpe 的穩健性。
    一個參數「脆弱」的定義：
      鄰近值之間的 Sharpe 差距 > default_sharpe × fragility_threshold
    """
    print(f"\n{'='*65}")
    print(f"  層次 2 — 參數敏感度掃描")
    print(f"{'='*65}")

    # 測試使用最近 N 個月
    if hasattr(benchmark_df.index[-1], 'date'):
        end_d = benchmark_df.index[-1].date()
    else:
        end_d = benchmark_df.index[-1]
    sens_months = 12 if fast else 18
    start_d = _add_months(end_d, -sens_months)
    print(f"  測試範圍: {start_d} ~ {end_d}  ({sens_months} 個月)\n")

    # ── 參數網格定義（單策略：純 MA 拉回） ──────────────────
    def default_strats():
        return [MAPullbackStrategy()]

    param_grid = [
        {
            'name': 'MA 回測最大持有天 (ma_max_hold)',
            'key': 'ma_max_hold',
            'values': [15, 20, 25, 30],
            'default_val': 20,
            'make_strats': lambda v: [MAPullbackStrategy(max_hold_days=v)],
            'make_risk':   lambda v: DEFAULT_RISK,
        },
        {
            'name': '單筆風險比例 (risk_per_trade)',
            'key': 'risk_per_trade',
            'values': [0.005, 0.01, 0.015, 0.02],
            'default_val': 0.01,
            'make_strats': lambda v: default_strats(),
            'make_risk':   lambda v: {**DEFAULT_RISK, 'risk_per_trade': v},
        },
        {
            'name': '最大同時持倉數 (max_positions)',
            'key': 'max_positions',
            'values': [3, 4, 6, 8],
            'default_val': 6,
            'make_strats': lambda v: default_strats(),
            'make_risk':   lambda v: {**DEFAULT_RISK, 'max_positions': v},
        },
    ]

    all_results = {}
    fragile_params = []

    for pg in param_grid:
        print(f"  📊 {pg['name']}")
        row = []
        for val in pg['values']:
            strats = pg['make_strats'](val)
            risk   = pg['make_risk'](val)
            r = _run_window(stocks_data, benchmark_df, start_d, end_d,
                            strats, risk=risk, label=f"{pg['key']}={val}")
            sh  = _safe(r, 'sharpe_ratio')
            pf  = _safe(r, 'profit_factor')
            wr  = _safe(r, 'win_rate_pct')
            mdd = _safe(r, 'max_drawdown_pct', -99.0)
            nt  = r.get('_n_trades', 0) if 'error' not in r else 0

            tag = ' ◄ 預設' if val == pg['default_val'] else ''
            print(f"    {str(val):>6}  Sharpe={sh:+.2f}  PF={pf:.2f}"
                  f"  WR={wr:.0f}%  MDD={mdd:.1f}%  交易={nt}筆{tag}")
            row.append({'value': val, 'sharpe': sh, 'pf': pf, 'wr': wr,
                        'mdd': mdd, 'n_trades': nt})

        # 脆弱度: 鄰近值 Sharpe 最大差距 / abs(default sharpe)
        sharpes = [x['sharpe'] for x in row]
        def_sh = next((x['sharpe'] for x in row if x['value'] == pg['default_val']), None)
        if def_sh is not None and abs(def_sh) > 0.05 and len(sharpes) > 1:
            max_diff = max(abs(sharpes[i] - sharpes[i+1]) / abs(def_sh)
                           for i in range(len(sharpes) - 1))
        else:
            max_diff = 0.0

        is_fragile = max_diff > PASS_THR['fragility_threshold']
        tag = '⚠️ 脆弱' if is_fragile else '✅ 穩健'
        print(f"    {tag}  (最大鄰近 Sharpe 差距: {max_diff*100:.0f}%)\n")

        if is_fragile:
            fragile_params.append(pg['name'])

        all_results[pg['key']] = {
            'name': pg['name'],
            'default': pg['default_val'],
            'results': row,
            'fragile': is_fragile,
            'max_neighbor_diff_pct': round(max_diff * 100, 1),
        }

    sens_pass = len(fragile_params) == 0
    print(f"  {'─'*55}")
    if fragile_params:
        print(f"  ⚠️ 脆弱參數 ({len(fragile_params)} 個):")
        for p in fragile_params:
            print(f"    → {p}")
    else:
        print(f"  ✅ 所有參數穩健，無脆弱點")
    print(f"  → 敏感度分析: {'✅ PASS' if sens_pass else '⚠️ CONDITIONAL (有脆弱參數)'}\n")

    return {
        'overall_pass': sens_pass,
        'fragile_params': fragile_params,
        'params': all_results,
    }


# ============================================================
# 最終裁決
# ============================================================
def print_verdict(wf: dict, sens: dict) -> dict:
    print(f"\n{'='*65}")
    print(f"  最終裁決")
    print(f"{'='*65}\n")

    checks = []

    if 'error' not in wf:
        avg_sh  = wf.get('avg_oos_sharpe', 0)
        avg_ret = wf.get('avg_retention', 0)
        avg_pf  = wf.get('avg_oos_pf', 0)
        avg_mdd = wf.get('avg_oos_mdd', -99)
        checks += [
            ('OOS Sharpe ≥ 0.7',     avg_sh  >= PASS_THR['min_sharpe_oos'],   f'{avg_sh:+.2f}'),
            ('OOS/IS 保留度 ≥ 50%',   avg_ret >= PASS_THR['min_is_oos_retention'], f'{avg_ret*100:.0f}%'),
            ('OOS Profit Factor ≥ 1.2', avg_pf >= PASS_THR['min_pf_oos'],     f'{avg_pf:.2f}'),
            ('OOS 最大回撤 ≥ -25%',   avg_mdd >= PASS_THR['max_mdd_oos'],     f'{avg_mdd:.1f}%'),
        ]

    if 'error' not in sens:
        n_f = len(sens.get('fragile_params', []))
        checks.append(('無脆弱參數 (0 個)', n_f == 0, f'{n_f} 個脆弱'))

    for desc, ok, val in checks:
        print(f"  {'✅' if ok else '❌'}  {desc:<38}  → {val}")

    pass_cnt   = sum(1 for _, ok, _ in checks if ok)
    total_chk  = len(checks)
    fail_cnt   = total_chk - pass_cnt

    print(f"\n  通過 {pass_cnt}/{total_chk} 項檢查\n")

    if   fail_cnt == 0:
        verdict = 'PASS'
        icon    = '🏆'
        msg     = '策略通過所有驗證門檻。建議進入 Paper Trading 至少 3 個月，再上真金。'
    elif fail_cnt == 1:
        verdict = 'CONDITIONAL'
        icon    = '⚠️'
        msg     = '策略接近通過，有 1 項未達標。針對弱項調整後重測。'
    elif fail_cnt <= 3:
        verdict = 'NEEDS WORK'
        icon    = '⚠️'
        msg     = f'策略有部分 edge，但 {fail_cnt} 項未達標。需要更多調整再做紙交易。'
    else:
        verdict = 'FAIL'
        icon    = '❌'
        msg     = 'OOS 大幅跑輸 IS，可能存在嚴重 curve-fit 或選股池偏差。'

    print(f"  {'─'*55}")
    print(f"  {icon}  裁決: {verdict}")
    print(f"  {msg}")
    print(f"{'='*65}\n")

    return {
        'verdict': verdict,
        'pass_count': pass_cnt,
        'total_checks': total_chk,
        'checks': [{'desc': d, 'pass': ok, 'value': v} for d, ok, v in checks],
        'message': msg,
    }


# ============================================================
# 資料下載
# ============================================================
def download_data(period: str = '3y') -> tuple:
    print(f"\n  📥 下載 {len(AI_TECH_STOCKS)} 檔股票 + {BENCHMARK}  ({period})...")
    tickers = [BENCHMARK] + AI_TECH_STOCKS
    df = yf.download(' '.join(tickers), period=period, interval='1d',
                     progress=False, group_by='ticker')
    if df.empty:
        return None, {}

    stocks: dict = {}
    failed: list = []
    for tk in AI_TECH_STOCKS:
        try:
            if tk in df.columns.levels[0]:
                tdf = df[tk].dropna(how='all').copy()
                if len(tdf) > 60:
                    stocks[tk] = tdf
                else:
                    failed.append(tk)
            else:
                failed.append(tk)
        except Exception:
            failed.append(tk)

    bm = None
    try:
        bm = df[BENCHMARK].dropna(how='all').copy()
        if isinstance(bm.columns, pd.MultiIndex):
            bm.columns = bm.columns.get_level_values(0)
    except Exception:
        pass

    if failed:
        print(f"  ⚠️ 跳過 {len(failed)} 檔: {', '.join(failed[:6])}")

    if bm is not None:
        d0 = bm.index[0].date() if hasattr(bm.index[0], 'date') else bm.index[0]
        d1 = bm.index[-1].date() if hasattr(bm.index[-1], 'date') else bm.index[-1]
        print(f"  ✅ {len(stocks)} 檔個股 | {BENCHMARK} {d0} ~ {d1}")
    return bm, stocks


# ============================================================
# 主程式
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='Anti-Gravity 策略驗證引擎')
    parser.add_argument('--wf-only',   action='store_true', help='只跑 walk-forward')
    parser.add_argument('--sens-only', action='store_true', help='只跑敏感度掃描')
    parser.add_argument('--fast',      action='store_true', help='快速模式 (2y 資料)')
    args = parser.parse_args()

    print(f"\n{'='*65}")
    print(f"  Anti-Gravity 策略驗證引擎")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*65}")

    period = '2y' if args.fast else '3y'
    bm, stocks = download_data(period)
    if bm is None or not stocks:
        print('❌ 資料下載失敗，中止。')
        return

    wf_result   = {'overall_pass': False, 'skipped': True}
    sens_result = {'overall_pass': False, 'skipped': True}

    if not args.sens_only:
        wf_result = run_walk_forward(stocks, bm, fast=args.fast)

    if not args.wf_only:
        sens_result = run_sensitivity_scan(stocks, bm, fast=args.fast)

    verdict = None
    if not args.wf_only and not args.sens_only:
        verdict = print_verdict(wf_result, sens_result)

    # ── 輸出 JSON ─────────────────────────────────────
    output = {
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'fast_mode': args.fast,
        'walk_forward': {k: v for k, v in wf_result.items() if k != 'windows'},
        'walk_forward_windows': wf_result.get('windows', []),
        'sensitivity': {k: v for k, v in sens_result.items() if k != 'params'},
        'sensitivity_params': sens_result.get('params', {}),
        'verdict': verdict,
    }
    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    out_path = os.path.join(DASHBOARD_DIR, 'validation_report.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2,
                  default=lambda x: str(x)
                  if not isinstance(x, (int, float, bool, type(None), list, dict))
                  else x)
    print(f"  💾 報告已寫入: {out_path}\n")


if __name__ == '__main__':
    main()
