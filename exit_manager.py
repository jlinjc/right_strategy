"""
exit_manager.py - Anti-Gravity 統一出場管理引擎
====================================================
三層出場框架：硬停損 → 移動停損 → 環境與時間。
純警報系統，不連接券商、不自動下單。

用法:
  python exit_manager.py                    # 單次掃描所有持倉
  python exit_manager.py --monitor          # 即時監控模式 (波段每小時/當沖每3分鐘)
  python exit_manager.py --add NVDA swing 135.50 14 130.20  # 新增持倉
  python exit_manager.py --close NVDA       # 確認出場 (歸檔)
  python exit_manager.py --list             # 列出所有持倉
  python exit_manager.py --test             # 執行內建測試
"""

import sys
import os
import json
import time
import argparse
import warnings
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Callable

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import yfinance as yf

from scanner_base import (
    AI_TECH_STOCKS, BENCHMARK, DASHBOARD_DIR, TOTAL_CAPITAL,
    calc_atr, calc_td_count, calc_vwap,
    calculate_market_breadth, send_line_notify,
)

POSITIONS_FILE = os.path.join(DASHBOARD_DIR, 'positions.json')
HISTORY_FILE = os.path.join(DASHBOARD_DIR, 'trade_history.json')


# ============================================================
# 持倉資料結構
# ============================================================
@dataclass
class LivePosition:
    """單一持倉的完整狀態"""
    ticker: str
    strategy: str            # 'hod', 'swing', 'momentum', 'buy_call'
    entry_date: str          # 'YYYY-MM-DD'
    entry_price: float
    shares: int
    initial_stop: float      # 進場時設定的停損
    current_stop: float      # 動態移動停損 (只升不降)
    highest_price: float     # 進場以來最高價
    days_held: int = 0
    notes: str = ''
    # 出場警報狀態
    exit_alert: str = ''     # 非空 = 已觸發出場警報 (等待手動確認)
    exit_alert_time: str = ''
    # 選擇權欄位
    option_expiry: str = ''
    option_strike: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> 'LivePosition':
        # 相容性: 忽略多餘的欄位
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid_fields}
        return cls(**filtered)


# ============================================================
# 出場規則配置 (不含 RSI)
# ============================================================
EXIT_RULES = {
    'hod': {
        'hard_stop': True,               # Layer 1: 初始停損
        'max_loss_pct': 8.0,             # Layer 1: 最大虧損 -8% 強制出場
        'trailing_atr_mult': 2.0,        # Layer 2: 高點 - 2×ATR
        'trailing_ma': None,             # Layer 2: 當沖不用均線移動停損
        'trailing_profit_pct': 50,       # Layer 2: 利潤回撤 50% 出場
        'vwap_exit': True,               # Layer 3: 跌破 VWAP 出場
        'breadth_tighten': True,         # Layer 3: 大盤惡化 → 收緊停損
        'eod_close': True,               # Layer 3: 收盤清倉
        'max_hold_days': 1,              # Layer 3: 最多持有 1 天
        'option_expiry_warning_days': 0,
    },
    'swing': {
        'hard_stop': True,
        'max_loss_pct': 8.0,
        'trailing_atr_mult': 2.0,
        'trailing_ma': 10,               # 連 2 天跌破 10MA 出場
        'trailing_profit_pct': 50,
        'vwap_exit': False,
        'breadth_tighten': True,
        'eod_close': False,
        'max_hold_days': 20,
        'option_expiry_warning_days': 0,
    },
    'momentum': {
        'hard_stop': True,
        'max_loss_pct': 8.0,
        'trailing_atr_mult': 2.0,
        'trailing_ma': 10,
        'trailing_profit_pct': 50,
        'vwap_exit': False,
        'breadth_tighten': True,
        'eod_close': False,
        'max_hold_days': 30,
        'option_expiry_warning_days': 0,
    },
    'buy_call': {
        'hard_stop': False,              # 選擇權最大損失 = 權利金
        'max_loss_pct': 50,              # 優化 #5: 權利金虧 50% 就出場
        'trailing_atr_mult': None,
        'trailing_ma': None,
        'trailing_profit_pct': 40,       # 優化 #5: 選擇權回撤門檻更嚴格
        'vwap_exit': False,
        'breadth_tighten': True,
        'eod_close': False,
        'max_hold_days': None,           # 由到期日控制
        'option_expiry_warning_days': 14, # 優化 #5: 14 天開始警告
        'option_theta_exit_dte': 7,      # 優化 #5: DTE ≤ 7 天 → 強制建議出場
    },
}


# ============================================================
# 自訂出場條件 Hook (預留 Fibonacci / Adam Theory 擴展)
# ============================================================
CUSTOM_EXIT_HOOKS: List[Callable] = []


def register_exit_hook(fn):
    """
    註冊自訂出場條件函式。
    fn(position: LivePosition, current_data: dict) -> Optional[str]
    回傳出場原因字串，或 None 表示不觸發。

    未來可在此插入:
      - Fibonacci 回測出場 (161.8% / 261.8% 擴展位)
      - Adam Theory 二次鏡像滿足點
    """
    CUSTOM_EXIT_HOOKS.append(fn)
    return fn


# ============================================================
# 持倉讀寫
# ============================================================
def load_positions() -> List[LivePosition]:
    """從 positions.json 載入持倉"""
    if not os.path.exists(POSITIONS_FILE):
        return []
    try:
        with open(POSITIONS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return [LivePosition.from_dict(p) for p in data.get('positions', [])]
    except Exception as e:
        print(f"⚠️ 載入持倉失敗: {e}")
        return []


def save_positions(positions: List[LivePosition]):
    """儲存持倉到 positions.json"""
    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    data = {
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total_positions': len(positions),
        'positions': [p.to_dict() for p in positions],
    }
    with open(POSITIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_trade_history() -> List[dict]:
    """載入已平倉交易歷史"""
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('trades', [])
    except Exception:
        return []


def save_trade_history(trades: List[dict]):
    """儲存已平倉交易歷史"""
    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    data = {
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total_trades': len(trades),
        'trades': trades,
    }
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# 持倉操作
# ============================================================
def add_position(ticker: str, strategy: str, entry_price: float,
                 shares: int, stop_loss: float, notes: str = '',
                 option_expiry: str = '', option_strike: float = 0.0):
    """新增一筆持倉"""
    positions = load_positions()

    # 檢查是否已有同一標的持倉
    existing = [p for p in positions if p.ticker == ticker.upper()]
    if existing:
        print(f"⚠️ {ticker} 已有持倉，無法重複新增。")
        return None

    pos = LivePosition(
        ticker=ticker.upper(),
        strategy=strategy,
        entry_date=datetime.now().strftime('%Y-%m-%d'),
        entry_price=entry_price,
        shares=shares,
        initial_stop=stop_loss,
        current_stop=stop_loss,
        highest_price=entry_price,
        days_held=0,
        notes=notes,
        option_expiry=option_expiry,
        option_strike=option_strike,
    )
    positions.append(pos)
    save_positions(positions)
    print(f"✅ 已新增持倉: {ticker} | {strategy} | ${entry_price} × {shares} 股 | 停損 ${stop_loss}")
    return pos


def confirm_exit(ticker: str, exit_price: float = None):
    """手動確認出場，將持倉歸檔到 trade_history.json"""
    positions = load_positions()
    history = load_trade_history()

    target = None
    remaining = []
    for p in positions:
        if p.ticker == ticker.upper():
            target = p
        else:
            remaining.append(p)

    if target is None:
        print(f"⚠️ 找不到 {ticker} 的持倉。")
        return

    # 如果沒有指定出場價，用最後的停損價作為預估
    if exit_price is None:
        exit_price = target.current_stop
        print(f"  ℹ️ 未指定出場價，使用移動停損價 ${exit_price:.2f} 作為預估")

    # 計算盈虧
    pnl = (exit_price - target.entry_price) * target.shares
    pnl_pct = (exit_price / target.entry_price - 1) * 100 if target.entry_price > 0 else 0

    trade_record = {
        'ticker': target.ticker,
        'strategy': target.strategy,
        'entry_date': target.entry_date,
        'entry_price': target.entry_price,
        'exit_date': datetime.now().strftime('%Y-%m-%d'),
        'exit_price': round(exit_price, 2),
        'shares': target.shares,
        'pnl': round(pnl, 2),
        'pnl_pct': round(pnl_pct, 2),
        'hold_days': target.days_held,
        'exit_reason': target.exit_alert or '手動出場',
        'notes': target.notes,
    }

    # --- 優化 #7: R-Multiple 追蹤 ---
    initial_risk = (target.entry_price - target.initial_stop) * target.shares
    if initial_risk > 0:
        r_multiple = round(pnl / initial_risk, 2)
    else:
        r_multiple = None
    trade_record['r_multiple'] = r_multiple
    trade_record['initial_risk'] = round(initial_risk, 2) if initial_risk > 0 else 0

    history.insert(0, trade_record)
    # 最多保留 200 筆歷史
    history = history[:200]

    save_positions(remaining)
    save_trade_history(history)

    emoji = '🟢' if pnl >= 0 else '🔴'
    r_str = f" | R={r_multiple:+.2f}" if r_multiple is not None else ""
    print(f"{emoji} 已確認出場: {target.ticker} | ${target.entry_price} → ${exit_price:.2f} | "
          f"P/L: ${pnl:+.2f} ({pnl_pct:+.1f}%){r_str} | 持有 {target.days_held} 天")
    return trade_record


# ============================================================
# 出場條件檢查
# ============================================================
def update_trailing_stop(pos: LivePosition, current_high: float,
                         atr: float, rules: dict, breadth: float) -> float:
    """
    更新移動停損 (只升不降)。
    包含：階梯式加速移動停損、保本停損、動態利潤回撤。
    回傳新的 current_stop。
    """
    new_stop = pos.current_stop

    # 更新最高價
    if current_high > pos.highest_price:
        pos.highest_price = current_high

    # 只有浮盈 > 0 且持有至少 2 天才啟動移動停損
    if pos.highest_price <= pos.entry_price or pos.days_held < 2:
        return new_stop

    gain_pct = (pos.highest_price / pos.entry_price - 1) * 100

    # --- 優化 #2: 階梯式加速 ATR 移動停損 ---
    # 漲越多停損越緊，大贏家不會回吐太多利潤
    if rules.get('trailing_atr_mult') and atr > 0:
        if gain_pct >= 30:
            effective_mult = 1.0     # 漲 30%+ → 只給 1×ATR 緩衝
        elif gain_pct >= 20:
            effective_mult = 1.25    # 漲 20%+ → 1.25×ATR
        elif gain_pct >= 10:
            effective_mult = 1.5     # 漲 10%+ → 1.5×ATR
        else:
            effective_mult = rules['trailing_atr_mult']  # 預設 2.0×ATR

        atr_stop = pos.highest_price - effective_mult * atr
        # 大盤廣度惡化 → 收緊為 1×ATR（覆蓋階梯）
        if rules.get('breadth_tighten') and breadth < 40:
            atr_stop = pos.highest_price - 1.0 * atr
        new_stop = max(new_stop, atr_stop)

    # --- 優化 #4: 動態利潤回撤保護 ---
    # 利潤越小回撤門檻越嚴格，微利不被回吐
    if rules.get('trailing_profit_pct'):
        max_profit = pos.highest_price - pos.entry_price
        if max_profit > 0:
            max_gain_pct = gain_pct
            if max_gain_pct >= 20:
                protect_ratio = 0.50   # 大贏: 保護 50% 利潤
            elif max_gain_pct >= 10:
                protect_ratio = 0.60   # 中贏: 保護 60%
            elif max_gain_pct >= 5:
                protect_ratio = 0.70   # 小贏: 保護 70%
            else:
                protect_ratio = 0.80   # 微利: 保護 80%（幾乎不給回撤）
            profit_stop = pos.entry_price + max_profit * protect_ratio
            new_stop = max(new_stop, profit_stop)

    # --- 優化 #6: 保本停損 ---
    # 獲利超過 1×ATR 後，停損至少拉到進場價
    gain = pos.highest_price - pos.entry_price
    if atr > 0 and gain >= atr and pos.days_held >= 2:
        breakeven_stop = pos.entry_price + 0.01  # 進場價 + $0.01
        new_stop = max(new_stop, breakeven_stop)

    # 只升不降
    new_stop = max(new_stop, pos.current_stop)
    return round(new_stop, 2)


def check_exit(pos: LivePosition, current_data: dict,
               rules: dict, breadth: float,
               daily_df: pd.DataFrame = None,
               valuation_data: dict = None) -> Optional[str]:
    """
    檢查單一持倉的所有出場條件。
    current_data: {'close': float, 'high': float, 'low': float, 'atr': float, 'vwap': float}
    回傳出場原因字串，或 None 表示不觸發。
    """
    close = current_data.get('close', 0)
    low = current_data.get('low', close)
    high = current_data.get('high', close)
    atr = current_data.get('atr', 0)
    vwap = current_data.get('vwap', 0)

    if close <= 0:
        return None

    # ============================
    # Layer 1: 硬停損 (最高優先級)
    # ============================

    # 1a. 跌破初始/移動停損
    if rules.get('hard_stop') and low <= pos.current_stop:
        return f"🔴 停損觸發 (${pos.current_stop:.2f})"

    # 1b. 單筆最大虧損
    max_loss_pct = rules.get('max_loss_pct', 8.0)
    loss_pct = (1 - close / pos.entry_price) * 100
    if loss_pct >= max_loss_pct:
        return f"🔴 最大虧損 -{loss_pct:.1f}% (上限 -{max_loss_pct}%)"

    # ============================
    # Layer 2: 移動停損 (保護利潤)
    # ============================

    # 更新移動停損
    new_stop = update_trailing_stop(pos, high, atr, rules, breadth)
    if new_stop > pos.current_stop:
        pos.current_stop = new_stop

    # 再檢查一次是否觸發了新的移動停損
    if rules.get('hard_stop') and low <= pos.current_stop and pos.current_stop > pos.initial_stop:
        return f"🟡 移動停損觸發 (${pos.current_stop:.2f}，最高曾到 ${pos.highest_price:.2f})"

    # 均線移動停損 (連 2 天跌破 10MA)
    if rules.get('trailing_ma') and daily_df is not None and pos.days_held >= 3:
        ma_window = rules['trailing_ma']
        if len(daily_df) >= ma_window + 1:
            ma_val = float(daily_df['Close'].rolling(ma_window).mean().iloc[-1])
            prev_close = float(daily_df['Close'].iloc[-2]) if len(daily_df) >= 2 else close
            prev_ma = float(daily_df['Close'].rolling(ma_window).mean().iloc[-2]) if len(daily_df) >= ma_window + 1 else ma_val

            if close < ma_val and prev_close < prev_ma:
                return f"🟡 連 2 天跌破 {ma_window}MA (${ma_val:.2f})"

    # 利潤回撤檢查 (在 update_trailing_stop 已處理停損更新，這裡做額外的描述性檢查)
    gain_pct = (close / pos.entry_price - 1) * 100 if pos.entry_price > 0 else 0
    if rules.get('trailing_profit_pct') and pos.highest_price > pos.entry_price:
        max_profit = pos.highest_price - pos.entry_price
        current_profit = close - pos.entry_price
        if max_profit > 0 and current_profit > 0:
            drawdown_pct = (1 - current_profit / max_profit) * 100
            threshold = rules['trailing_profit_pct']
            if drawdown_pct >= threshold and pos.days_held >= 2:
                return f"🟡 利潤回撤 {drawdown_pct:.0f}% (最高盈利 ${max_profit:.2f} → 當前 ${current_profit:.2f})"



    # ============================
    # Layer 3: 環境與時間
    # ============================

    # VWAP 出場 (僅當沖)
    if rules.get('vwap_exit') and vwap > 0 and close < vwap:
        return f"⏰ 跌破 VWAP (${vwap:.2f})"

    # 最大持有天數
    max_days = rules.get('max_hold_days')
    if max_days and pos.days_held >= max_days:
        return f"⏰ 已持有 {pos.days_held} 天 (上限 {max_days} 天)"

    # --- 優化 #5: 選擇權 Theta 加速衰減出場 ---
    if pos.option_expiry:
        try:
            expiry_date = datetime.strptime(pos.option_expiry, '%Y-%m-%d').date()
            dte = (expiry_date - date.today()).days

            theta_exit_dte = rules.get('option_theta_exit_dte', 7)
            warning_days = rules.get('option_expiry_warning_days', 5)

            # Theta 強制出場：DTE ≤ 7 天且獲利不足 20%
            if dte <= theta_exit_dte and gain_pct < 20:
                return (f"⏰ 選擇權 DTE={dte} 天 + 獲利僅 {gain_pct:+.1f}%，"
                        f"Theta 加速衰減風險，建議出場")

            # Theta 虧損出場：DTE ≤ 14 天且處於虧損
            if dte <= warning_days and gain_pct < 0:
                return (f"⏰ 選擇權 DTE={dte} 天且虧損 {gain_pct:.1f}%，"
                        f"建議止損出場")

            # 一般到期警告
            if dte <= warning_days:
                return f"⏰ 選擇權即將到期 (剩 {dte} 天，到期日 {pos.option_expiry})"
        except ValueError:
            pass

    # --- 優化 #3: 估值天花板出場 ---
    # 接近估值昂貴區時警告
    if valuation_data:
        expensive = valuation_data.get('expensive_price')
        fair = valuation_data.get('fair_value')
        if expensive and close >= expensive:
            return (f"📊 現價已達估值昂貴區 (${close:.2f} ≥ 昂貴價 "
                    f"${expensive:.2f})，基本面不支持繼續持有")
        elif fair and close >= fair * 1.10:
            return (f"📊 現價已超出合理價 10%+ (${close:.2f} vs "
                    f"合理價 ${fair:.2f})，考慮減碼")

    # 收盤清倉 (當沖)
    # 注意: 這需要知道當前是否接近收盤，在 scan_exits 中判斷

    # ============================
    # 自訂出場 Hook (Fibonacci / Adam Theory 等)
    # ============================
    for hook in CUSTOM_EXIT_HOOKS:
        try:
            result = hook(pos, current_data)
            if result:
                return f"🔮 {result}"
        except Exception:
            pass

    # --- 優化 #1: 分批停利警報 (最低優先級，建議性) ---
    # 達到目標百分比時，建議部分賣出
    if gain_pct >= 30 and pos.shares > 1:
        return f"🎯 第二停利目標 +{gain_pct:.1f}%，建議賣出 {pos.shares // 2}/{pos.shares} 股 (剩餘用移動停損)"
    elif gain_pct >= 15 and pos.shares > 1:
        sell_qty = pos.shares // 2
        return f"🎯 第一停利目標 +{gain_pct:.1f}%，建議賣出 {sell_qty}/{pos.shares} 股 (停損拉至保本)"

    return None


# ============================================================
# 出場警報格式化
# ============================================================
def format_exit_alert(pos: LivePosition, reason: str,
                      current_price: float) -> str:
    """格式化出場警報文字"""
    pnl = (current_price - pos.entry_price) * pos.shares
    pnl_pct = (current_price / pos.entry_price - 1) * 100 if pos.entry_price > 0 else 0
    max_pnl = (pos.highest_price - pos.entry_price) * pos.shares
    max_pnl_pct = (pos.highest_price / pos.entry_price - 1) * 100 if pos.entry_price > 0 else 0

    strategy_labels = {
        'hod': '日內突破 HOD',
        'swing': '波段回調',
        'momentum': '動能突破',
        'buy_call': 'Buy Call 選擇權',
    }
    strategy_label = strategy_labels.get(pos.strategy, pos.strategy)

    emoji = '🟢' if pnl >= 0 else '🔴'

    alert = (
        f"\n📤 【Anti-Gravity 出場警報】\n"
        f"──────────────────────────────\n"
        f"{reason} ➔ {pos.ticker}\n"
        f"⏱️ 時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"──────────────────────────────\n"
        f"📊 持倉狀態：\n"
        f" ├─ 策略：{strategy_label}\n"
        f" ├─ 進場：{pos.entry_date} @ ${pos.entry_price:.2f} × {pos.shares} 股\n"
        f" ├─ 持有：{pos.days_held} 天\n"
        f" ├─ 最高曾到：${pos.highest_price:.2f}\n"
        f" └─ 當前價格：${current_price:.2f}\n"
        f"──────────────────────────────\n"
        f"💰 損益：\n"
        f" ├─ 浮動盈虧：{emoji} ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
        f" ├─ 最高浮盈：${max_pnl:+.2f} ({max_pnl_pct:+.1f}%)\n"
        f" └─ 移動停損：${pos.current_stop:.2f}\n"
        f"──────────────────────────────\n"
        f"⚡ 建議動作：賣出 {pos.shares} 股 {pos.ticker}\n"
    )

    # 選擇權額外資訊
    if pos.option_expiry:
        try:
            dte = (datetime.strptime(pos.option_expiry, '%Y-%m-%d').date() - date.today()).days
            alert += f"   選擇權到期：{pos.option_expiry} (剩 {dte} 天)\n"
        except ValueError:
            pass

    return alert


# ============================================================
# 主掃描函式
# ============================================================
def scan_exits() -> List[str]:
    """
    遍歷所有持倉，下載最新數據，檢查出場條件。
    回傳所有出場警報文字列表。
    """
    positions = load_positions()
    if not positions:
        print("  ℹ️ 目前無持倉。")
        return []

    # 過濾掉已有出場警報但尚未確認的持倉 (不重複發警報)
    active = [p for p in positions if not p.exit_alert]
    pending = [p for p in positions if p.exit_alert]

    if pending:
        print(f"  ⚠️ {len(pending)} 筆持倉已有出場警報待確認: "
              f"{', '.join(p.ticker for p in pending)}")

    if not active:
        print("  ℹ️ 所有持倉都已有出場警報，等待手動確認。")
        return []

    # 下載數據
    tickers = list(set(p.ticker for p in active))
    print(f"  📥 下載 {len(tickers)} 檔股票最新數據...")

    alerts = []

    # 計算大盤廣度
    breadth = calculate_market_breadth()

    # --- 優化 #3: 載入估值數據 ---
    fundamentals = {}
    fundamentals_file = os.path.join(DASHBOARD_DIR, 'fundamentals_data.json')
    if os.path.exists(fundamentals_file):
        try:
            with open(fundamentals_file, 'r', encoding='utf-8') as f:
                fund_data = json.load(f)
            for ticker, stock_data in fund_data.get('stocks', {}).items():
                if isinstance(stock_data, dict) and 'valuation' in stock_data:
                    fundamentals[ticker] = stock_data['valuation']
        except Exception:
            pass

    # 下載日線數據 (用於均線計算)
    tickers_str = " ".join(tickers)
    try:
        daily_data = yf.download(tickers_str, period="45d", interval="1d",
                                 progress=False, group_by='ticker')
    except Exception as e:
        print(f"  ⚠️ 日線數據下載失敗: {e}")
        daily_data = pd.DataFrame()

    # 下載日內數據 (用於 VWAP，僅當沖策略需要)
    has_intraday = any(p.strategy == 'hod' for p in active)
    intraday_data = {}
    if has_intraday:
        try:
            intra_df = yf.download(tickers_str, period="1d", interval="3m",
                                   progress=False, group_by='ticker')
            if not intra_df.empty:
                for ticker in tickers:
                    try:
                        if len(tickers) == 1:
                            tk_df = intra_df.copy()
                        elif ticker in intra_df.columns.levels[0]:
                            tk_df = intra_df[ticker].dropna(how='all')
                        else:
                            continue
                        if not tk_df.empty:
                            vwap_series = calc_vwap(tk_df)
                            intraday_data[ticker] = {
                                'vwap': float(vwap_series.iloc[-1]) if not vwap_series.empty else 0
                            }
                    except Exception:
                        pass
        except Exception:
            pass

    # 逐一檢查
    for pos in active:
        ticker = pos.ticker
        rules = EXIT_RULES.get(pos.strategy, EXIT_RULES['swing'])

        # 取得日線數據
        daily_df = None
        try:
            if len(tickers) == 1 and not daily_data.empty:
                daily_df = daily_data.copy()
                if isinstance(daily_df.columns, pd.MultiIndex):
                    daily_df.columns = daily_df.columns.get_level_values(0)
            elif not daily_data.empty and ticker in daily_data.columns.levels[0]:
                daily_df = daily_data[ticker].dropna(how='all')
        except Exception:
            pass

        if daily_df is None or daily_df.empty or len(daily_df) < 2:
            print(f"  ⚠️ {ticker} 無可用日線數據，跳過")
            continue

        # 構建 current_data
        close = float(daily_df['Close'].iloc[-1])
        high = float(daily_df['High'].iloc[-1])
        low = float(daily_df['Low'].iloc[-1])

        # ATR
        atr_series = calc_atr(daily_df['High'], daily_df['Low'], daily_df['Close'], 14)
        atr = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else close * 0.03

        # VWAP (僅當沖)
        vwap = intraday_data.get(ticker, {}).get('vwap', 0)

        current_data = {
            'close': close,
            'high': high,
            'low': low,
            'atr': atr,
            'vwap': vwap,
        }

        # 更新持有天數
        try:
            entry_dt = datetime.strptime(pos.entry_date, '%Y-%m-%d').date()
            pos.days_held = (date.today() - entry_dt).days
        except ValueError:
            pass

        # 更新最高價
        pos.highest_price = max(pos.highest_price, high)

        # 檢查出場 (傳入 valuation_data)
        valuation_data = fundamentals.get(ticker)
        exit_reason = check_exit(pos, current_data, rules, breadth, daily_df,
                                 valuation_data=valuation_data)

        if exit_reason:
            alert_text = format_exit_alert(pos, exit_reason, close)
            alerts.append(alert_text)
            print(alert_text)

            # 標記出場警報 (不移除，等手動確認)
            pos.exit_alert = exit_reason
            pos.exit_alert_time = datetime.now().strftime('%Y-%m-%d %H:%M')
        else:
            # 正常持倉狀態更新
            pnl_pct = (close / pos.entry_price - 1) * 100 if pos.entry_price > 0 else 0
            status = '🟢' if pnl_pct >= 0 else '🔴'
            print(f"  {status} {ticker:6s} | ${close:.2f} | P/L {pnl_pct:+.1f}% | "
                  f"停損 ${pos.current_stop:.2f} | 持有 {pos.days_held}天")

    # 儲存更新後的持倉
    save_positions(positions)

    return alerts


# ============================================================
# 持倉列表
# ============================================================
def list_positions():
    """印出所有持倉"""
    positions = load_positions()
    if not positions:
        print("\n  ℹ️ 目前無持倉。\n")
        return

    print(f"\n{'═' * 60}")
    print(f"  Anti-Gravity 持倉列表 ({len(positions)} 筆)")
    print(f"{'═' * 60}")

    total_cost = 0
    total_value = 0

    for pos in positions:
        alert_tag = " ⚠️ 待出場" if pos.exit_alert else ""
        print(f"\n  📌 {pos.ticker} — {pos.strategy}{alert_tag}")
        print(f"     進場：{pos.entry_date} @ ${pos.entry_price:.2f} × {pos.shares} 股")
        print(f"     停損：初始 ${pos.initial_stop:.2f} → 當前 ${pos.current_stop:.2f}")
        print(f"     最高：${pos.highest_price:.2f} | 持有 {pos.days_held} 天")
        if pos.exit_alert:
            print(f"     ⚠️ 出場警報：{pos.exit_alert} ({pos.exit_alert_time})")
        if pos.notes:
            print(f"     備註：{pos.notes}")
        if pos.option_expiry:
            try:
                dte = (datetime.strptime(pos.option_expiry, '%Y-%m-%d').date() - date.today()).days
                print(f"     選擇權：到期 {pos.option_expiry} (剩 {dte} 天)")
            except ValueError:
                pass

        total_cost += pos.entry_price * pos.shares

    print(f"\n  {'─' * 40}")
    print(f"  總投入成本: ${total_cost:,.2f}")
    print(f"{'═' * 60}\n")

    # 印出交易歷史摘要
    history = load_trade_history()
    if history:
        wins = [t for t in history if t.get('pnl', 0) > 0]
        losses = [t for t in history if t.get('pnl', 0) <= 0]
        total_pnl = sum(t.get('pnl', 0) for t in history)
        win_rate = len(wins) / len(history) * 100 if history else 0

        print(f"  📊 交易歷史: {len(history)} 筆 | 勝率 {win_rate:.0f}% | 累計 P/L ${total_pnl:+,.2f}")

        # --- 優化 #7: R-Multiple 統計 ---
        r_values = [t['r_multiple'] for t in history if t.get('r_multiple') is not None]
        if r_values:
            avg_r = sum(r_values) / len(r_values)
            max_r = max(r_values)
            min_r = min(r_values)
            print(f"  🏆 R-Multiple: 平均 {avg_r:+.2f}R | 最佳 {max_r:+.2f}R | 最差 {min_r:+.2f}R")


# ============================================================
# 即時監控模式
# ============================================================
def start_exit_monitor():
    """即時監控模式"""
    print(f"\n{'═' * 55}")
    print(f"  Anti-Gravity | 出場監控引擎")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═' * 55}")
    print(f"  波段持倉：每小時掃描一次")
    print(f"  當沖持倉：每 3 分鐘掃描一次")
    print(f"  按 Ctrl+C 停止\n")

    last_swing_scan = 0
    SWING_INTERVAL = 3600      # 1 小時
    INTRADAY_INTERVAL = 180    # 3 分鐘

    while True:
        try:
            positions = load_positions()
            has_intraday = any(p.strategy == 'hod' and not p.exit_alert for p in positions)
            has_swing = any(p.strategy != 'hod' and not p.exit_alert for p in positions)
            now = time.time()

            should_scan = False
            if has_intraday:
                should_scan = True  # 當沖隨時掃
            if has_swing and (now - last_swing_scan) >= SWING_INTERVAL:
                should_scan = True

            if should_scan:
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🔍 執行出場掃描...")
                alerts = scan_exits()

                if alerts:
                    # 發送 LINE 通知
                    msg = "\n".join(alerts)
                    send_line_notify(msg)

                if has_swing:
                    last_swing_scan = now
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ⏳ 等待中...")

        except KeyboardInterrupt:
            print("\n🛑 出場監控已停止。")
            break
        except Exception as e:
            print(f"\n⚠️ 監控發生錯誤: {e}")

        time.sleep(INTRADAY_INTERVAL if has_intraday else 60)


# ============================================================
# 內建測試
# ============================================================
def run_tests():
    """執行內建測試，驗證所有出場規則"""
    print(f"\n{'═' * 55}")
    print(f"  Anti-Gravity 出場引擎 — 單元測試")
    print(f"{'═' * 55}\n")

    passed = 0
    failed = 0

    def assert_test(name, condition):
        nonlocal passed, failed
        if condition:
            print(f"  ✅ {name}")
            passed += 1
        else:
            print(f"  ❌ {name}")
            failed += 1

    # 測試 1: 硬停損
    print("  📋 Layer 1: 硬停損")
    pos = LivePosition(
        ticker='TEST', strategy='swing', entry_date='2026-01-01',
        entry_price=100.0, shares=10, initial_stop=95.0,
        current_stop=95.0, highest_price=100.0,
    )
    rules = EXIT_RULES['swing']
    data = {'close': 94.0, 'high': 100.0, 'low': 94.0, 'atr': 3.0, 'vwap': 0}
    result = check_exit(pos, data, rules, breadth=60.0)
    assert_test("跌破初始停損 → 觸發", result is not None and "停損" in result)

    # 測試 2: 最大虧損
    pos2 = LivePosition(
        ticker='TEST2', strategy='swing', entry_date='2026-01-01',
        entry_price=100.0, shares=10, initial_stop=80.0,
        current_stop=80.0, highest_price=100.0,
    )
    data2 = {'close': 91.0, 'high': 92.0, 'low': 91.0, 'atr': 3.0, 'vwap': 0}
    result2 = check_exit(pos2, data2, rules, breadth=60.0)
    assert_test("虧損 -9% > 上限 -8% → 觸發", result2 is not None and "最大虧損" in result2)

    # 測試 3: 移動停損只升不降
    print("\n  📋 Layer 2: 移動停損")
    pos3 = LivePosition(
        ticker='TEST3', strategy='swing', entry_date='2026-01-01',
        entry_price=100.0, shares=10, initial_stop=95.0,
        current_stop=95.0, highest_price=110.0, days_held=5,
    )
    stop1 = update_trailing_stop(pos3, 110.0, 3.0, rules, breadth=60.0)
    assert_test(f"最高 $110, ATR $3 → 停損升至 ${stop1:.2f} (> $95)", stop1 > 95.0)

    old_stop = stop1
    pos3.current_stop = stop1
    stop2 = update_trailing_stop(pos3, 108.0, 3.0, rules, breadth=60.0)
    assert_test(f"價格回落到 $108 → 停損不降 (${stop2:.2f} >= ${old_stop:.2f})", stop2 >= old_stop)

    # 測試 4: 大盤廣度收緊
    pos4 = LivePosition(
        ticker='TEST4', strategy='swing', entry_date='2026-01-01',
        entry_price=100.0, shares=10, initial_stop=90.0,
        current_stop=90.0, highest_price=115.0, days_held=5,
    )
    stop_normal = update_trailing_stop(pos4, 115.0, 3.0, rules, breadth=60.0)
    pos4.current_stop = 90.0  # 重置
    stop_tight = update_trailing_stop(pos4, 115.0, 3.0, rules, breadth=30.0)
    assert_test(f"廣度 <40% → 停損收緊 (${stop_tight:.2f} > ${stop_normal:.2f})",
                stop_tight > stop_normal)

    # 測試 5: 利潤回撤
    pos5 = LivePosition(
        ticker='TEST5', strategy='swing', entry_date='2026-01-01',
        entry_price=100.0, shares=10, initial_stop=95.0,
        current_stop=95.0, highest_price=120.0, days_held=5,
    )
    data5 = {'close': 108.0, 'high': 109.0, 'low': 107.0, 'atr': 3.0, 'vwap': 0}
    result5 = check_exit(pos5, data5, rules, breadth=60.0)
    assert_test("最高 $120 → 回落到 $108 (利潤 $8/$20 = 回撤 60%) → 觸發",
                result5 is not None and ("利潤回撤" in result5 or "停損" in result5))

    # 測試 6: 最大持有天數
    print("\n  📋 Layer 3: 環境與時間")
    pos6 = LivePosition(
        ticker='TEST6', strategy='swing', entry_date='2026-01-01',
        entry_price=100.0, shares=10, initial_stop=95.0,
        current_stop=95.0, highest_price=105.0, days_held=21,
    )
    data6 = {'close': 105.0, 'high': 105.0, 'low': 104.0, 'atr': 3.0, 'vwap': 0}
    result6 = check_exit(pos6, data6, rules, breadth=60.0)
    assert_test("持有 21 天 > 上限 20 天 → 觸發", result6 is not None and "已持有" in result6)

    # 測試 7: VWAP 出場 (當沖)
    hod_rules = EXIT_RULES['hod']
    pos7 = LivePosition(
        ticker='TEST7', strategy='hod', entry_date='2026-01-01',
        entry_price=100.0, shares=10, initial_stop=98.0,
        current_stop=98.0, highest_price=102.0, days_held=0,
    )
    data7 = {'close': 99.5, 'high': 102.0, 'low': 99.0, 'atr': 2.0, 'vwap': 100.0}
    result7 = check_exit(pos7, data7, hod_rules, breadth=60.0)
    assert_test("當沖跌破 VWAP ($99.5 < $100) → 觸發", result7 is not None and "VWAP" in result7)

    # 測試 8: 選擇權 Theta 出場 (優化 #5)
    call_rules = EXIT_RULES['buy_call']
    pos8 = LivePosition(
        ticker='TEST8', strategy='buy_call', entry_date='2026-01-01',
        entry_price=5.0, shares=1, initial_stop=0.0,
        current_stop=0.0, highest_price=5.0, days_held=20,
        option_expiry=(date.today() + timedelta(days=3)).strftime('%Y-%m-%d'),
    )
    data8 = {'close': 5.0, 'high': 5.0, 'low': 5.0, 'atr': 0.5, 'vwap': 0}
    result8 = check_exit(pos8, data8, call_rules, breadth=60.0)
    assert_test("選擇權 DTE=3 天 + 獲利<20% → Theta 出場觸發",
                result8 is not None and ("Theta" in result8 or "DTE" in result8))

    # 測試 8b: 選擇權 DTE≤14 且虧損 → 止損出場
    pos8b = LivePosition(
        ticker='TEST8B', strategy='buy_call', entry_date='2026-01-01',
        entry_price=10.0, shares=1, initial_stop=0.0,
        current_stop=0.0, highest_price=10.0, days_held=30,
        option_expiry=(date.today() + timedelta(days=10)).strftime('%Y-%m-%d'),
    )
    data8b = {'close': 8.0, 'high': 8.5, 'low': 7.5, 'atr': 1.0, 'vwap': 0}
    result8b = check_exit(pos8b, data8b, call_rules, breadth=60.0)
    assert_test("選擇權 DTE=10 天 + 虧損 → 止損出場觸發",
                result8b is not None and "虧損" in result8b)

    # 測試 9: 自訂 Hook
    print("\n  📋 擴展接口")

    @register_exit_hook
    def test_hook(pos, data):
        if data.get('close', 0) > 200:
            return "測試 Hook: 價格超過 $200"
        return None

    pos9 = LivePosition(
        ticker='TEST9', strategy='buy_call', entry_date='2026-01-01',
        entry_price=150.0, shares=10, initial_stop=0.0,
        current_stop=0.0, highest_price=150.0, days_held=5,
    )
    data9 = {'close': 205.0, 'high': 210.0, 'low': 200.0, 'atr': 5.0, 'vwap': 0}
    result9 = check_exit(pos9, data9, call_rules, breadth=60.0)
    assert_test("自訂 Hook 觸發 (價格 > $200)", result9 is not None and "Hook" in result9)

    # 清理 Hook
    CUSTOM_EXIT_HOOKS.clear()

    # ============================
    # 新增: 優化功能測試
    # ============================
    print("\n  📋 優化 #2: 階梯式加速移動停損")
    # 漲 30%+ 時應使用 1×ATR (更緊)
    pos_tier = LivePosition(
        ticker='TIER', strategy='swing', entry_date='2026-01-01',
        entry_price=100.0, shares=10, initial_stop=90.0,
        current_stop=90.0, highest_price=135.0, days_held=10,
    )
    stop_tiered = update_trailing_stop(pos_tier, 135.0, 3.0, rules, breadth=60.0)
    # 漲 35% → 1×ATR → stop = 135 - 3 = 132
    assert_test(f"漲 35% → 1×ATR 停損 ${stop_tiered:.2f} (≈$132)", stop_tiered >= 131.0)

    # 漲 10%+ 時應使用 1.5×ATR
    pos_tier2 = LivePosition(
        ticker='TIER2', strategy='swing', entry_date='2026-01-01',
        entry_price=100.0, shares=10, initial_stop=90.0,
        current_stop=90.0, highest_price=112.0, days_held=5,
    )
    stop_tiered2 = update_trailing_stop(pos_tier2, 112.0, 3.0, rules, breadth=60.0)
    # 漲 12% → 1.5×ATR → stop = 112 - 4.5 = 107.5
    assert_test(f"漲 12% → 1.5×ATR 停損 ${stop_tiered2:.2f} (≈$107.5)", stop_tiered2 >= 107.0)

    print("\n  📋 優化 #6: 保本停損")
    # 獲利 >= 1×ATR 後，停損至少在進場價
    pos_be = LivePosition(
        ticker='BE', strategy='swing', entry_date='2026-01-01',
        entry_price=100.0, shares=10, initial_stop=95.0,
        current_stop=95.0, highest_price=106.0, days_held=3,
    )
    stop_be = update_trailing_stop(pos_be, 106.0, 3.0, rules, breadth=60.0)
    assert_test(f"獲利 ${106-100}=$6 ≥ 1×ATR=$3 → 停損 ≥ $100.01 (${stop_be:.2f})",
                stop_be >= 100.01)

    print("\n  📋 優化 #4: 動態利潤回撤保護")
    # 微利 (4%) 保護 80%
    pos_dp = LivePosition(
        ticker='DP', strategy='swing', entry_date='2026-01-01',
        entry_price=100.0, shares=10, initial_stop=95.0,
        current_stop=95.0, highest_price=104.0, days_held=3,
    )
    stop_dp = update_trailing_stop(pos_dp, 104.0, 3.0, rules, breadth=60.0)
    # 微利 4% → protect 80% → stop = 100 + 4 * 0.80 = 103.20
    assert_test(f"微利 4% → 保護 80% → 停損 ≈$103.2 (${stop_dp:.2f})", stop_dp >= 103.0)

    print("\n  📋 優化 #1: 分批停利警報")
    # 漲 15%+ → 第一停利目標
    pos_sp = LivePosition(
        ticker='SP', strategy='swing', entry_date='2026-01-01',
        entry_price=100.0, shares=20, initial_stop=95.0,
        current_stop=112.0, highest_price=118.0, days_held=8,
    )
    data_sp = {'close': 116.0, 'high': 118.0, 'low': 115.0, 'atr': 3.0, 'vwap': 0}
    result_sp = check_exit(pos_sp, data_sp, rules, breadth=60.0)
    assert_test("漲 16% → 第一停利目標觸發",
                result_sp is not None and "第一停利" in result_sp)

    # 漲 30%+ → 第二停利目標
    pos_sp2 = LivePosition(
        ticker='SP2', strategy='swing', entry_date='2026-01-01',
        entry_price=100.0, shares=20, initial_stop=95.0,
        current_stop=129.0, highest_price=131.0, days_held=12,
    )
    # ATR=1 → 1×ATR stop=$130, low=$130.5 > $130 → 不觸發停損
    data_sp2 = {'close': 131.0, 'high': 131.0, 'low': 130.5, 'atr': 1.0, 'vwap': 0}
    result_sp2 = check_exit(pos_sp2, data_sp2, rules, breadth=60.0)
    assert_test("漲 31% → 第二停利目標觸發",
                result_sp2 is not None and "第二停利" in result_sp2)

    print("\n  📋 優化 #3: 估值天花板出場")
    pos_val = LivePosition(
        ticker='VAL', strategy='swing', entry_date='2026-01-01',
        entry_price=200.0, shares=10, initial_stop=185.0,
        current_stop=256.0, highest_price=260.0, days_held=15,
    )
    # ATR=2 → 1×ATR stop=$258, low=$259 > $258 → 不觸發停損
    data_val = {'close': 260.0, 'high': 260.0, 'low': 259.0, 'atr': 2.0, 'vwap': 0}
    valuation = {'fair_value': 220.0, 'expensive_price': 250.0}
    result_val = check_exit(pos_val, data_val, rules, breadth=60.0,
                            valuation_data=valuation)
    assert_test("現價 $260 ≥ 昂貴價 $250 → 估值出場觸發",
                result_val is not None and "昂貴" in result_val)

    # 超出合理價 10%+
    pos_val2 = LivePosition(
        ticker='VAL2', strategy='swing', entry_date='2026-01-01',
        entry_price=200.0, shares=10, initial_stop=185.0,
        current_stop=241.0, highest_price=245.0, days_held=10,
    )
    # ATR=2 → 1×ATR stop=$243, low=$244 > $243 → 不觸發停損
    data_val2 = {'close': 244.0, 'high': 245.0, 'low': 244.0, 'atr': 2.0, 'vwap': 0}
    valuation2 = {'fair_value': 220.0, 'expensive_price': 260.0}
    result_val2 = check_exit(pos_val2, data_val2, rules, breadth=60.0,
                             valuation_data=valuation2)
    assert_test("現價 $244 ≥ 合理價 $220 × 1.1 = $242 → 考慮減碼",
                result_val2 is not None and "合理價" in result_val2)

    print("\n  📋 優化 #7: R-Multiple 追蹤")
    # R-Multiple = PnL / Initial Risk
    # 進場 $100, 停損 $95 → 風險 = $5 × 10股 = $50
    # 出場 $115 → PnL = $15 × 10 = $150 → R = 150/50 = 3.0
    initial_risk = (100.0 - 95.0) * 10
    pnl = (115.0 - 100.0) * 10
    r_mult = round(pnl / initial_risk, 2)
    assert_test(f"R-Multiple 計算: PnL=${pnl} / 風險=${initial_risk} = {r_mult:.2f}R",
                r_mult == 3.0)

    # 測試 CRUD (原測試 10)
    print("\n  📋 持倉讀寫")
    # 備份
    backup_positions = load_positions()

    test_positions = [
        LivePosition(
            ticker='CRUD_TEST', strategy='swing', entry_date='2026-01-01',
            entry_price=100.0, shares=5, initial_stop=95.0,
            current_stop=95.0, highest_price=100.0,
        )
    ]
    save_positions(test_positions)
    loaded = load_positions()
    assert_test("儲存 → 載入 → 資料一致",
                len(loaded) == 1 and loaded[0].ticker == 'CRUD_TEST')

    # 還原
    save_positions(backup_positions)

    # 結果
    print(f"\n{'─' * 55}")
    total = passed + failed
    if failed == 0:
        print(f"  🎉 全部通過！ ({passed}/{total})")
    else:
        print(f"  ⚠️ {failed} 個測試失敗 ({passed}/{total} 通過)")
    print(f"{'═' * 55}\n")


# ============================================================
# CLI 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='Anti-Gravity 出場管理引擎')
    parser.add_argument('--monitor', action='store_true', help='啟動即時監控模式')
    parser.add_argument('--add', nargs='+', metavar='ARG',
                        help='新增持倉: TICKER STRATEGY PRICE SHARES STOP [NOTES]')
    parser.add_argument('--close', nargs='+', metavar='ARG',
                        help='確認出場: TICKER [EXIT_PRICE]')
    parser.add_argument('--list', action='store_true', help='列出所有持倉')
    parser.add_argument('--test', action='store_true', help='執行內建測試')

    args = parser.parse_args()

    if args.test:
        run_tests()
    elif args.list:
        list_positions()
    elif args.add:
        if len(args.add) < 5:
            print("用法: --add TICKER STRATEGY PRICE SHARES STOP [NOTES]")
            print("  STRATEGY: hod, swing, momentum, buy_call")
            print("  範例: --add NVDA swing 135.50 14 130.20 'TD-9 下跌竭盡'")
            return
        ticker = args.add[0]
        strategy = args.add[1]
        price = float(args.add[2])
        shares = int(args.add[3])
        stop = float(args.add[4])
        notes = args.add[5] if len(args.add) > 5 else ''
        add_position(ticker, strategy, price, shares, stop, notes)
    elif args.close:
        ticker = args.close[0]
        exit_price = float(args.close[1]) if len(args.close) > 1 else None
        confirm_exit(ticker, exit_price)
    elif args.monitor:
        start_exit_monitor()
    else:
        # 預設: 單次掃描
        print(f"\n{'═' * 55}")
        print(f"  Anti-Gravity | 出場掃描")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'═' * 55}\n")
        alerts = scan_exits()
        if alerts:
            print(f"\n  📲 共 {len(alerts)} 筆出場警報")
        else:
            print(f"\n  ✅ 所有持倉正常，無出場訊號。")
        print()


if __name__ == '__main__':
    main()
