# [LOCAL VERSION DIFF]: 全新加入的檔案。回測系統的核心引擎，負責處理歷史 K 線資料與模擬交易執行。
"""
backtest_engine.py - Anti-Gravity 事件驅動回測引擎
=====================================================
通用的回測框架，與策略無關。

核心類別：
  - Signal:              策略產生的交易訊號
  - Position:            單一持倉的生命週期追蹤
  - Trade:               已平倉的完整交易紀錄
  - Portfolio:           投資組合管理（現金、持倉、淨值）
  - RiskManager:         風控檢查 + 倉位計算
  - PerformanceAnalyzer: 績效指標計算（Sharpe / MDD / PF / 月報）

設計原則：
  - 不偷看未來數據 (no look-ahead bias)
  - 訊號在收盤後產生，以【次日開盤價】成交
  - 內建滑點 (0.05%) 和手續費 ($1/筆) 模型
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional, Dict, List, Tuple


# ============================================================
# Signal — 策略產生的交易訊號
# ============================================================
@dataclass
class Signal:
    date: date              # 訊號觸發日（收盤後判定）
    ticker: str             # 股票代碼
    direction: str          # 'long' or 'short'
    strategy: str           # 策略名稱 (e.g. 'td9_buy')
    entry_price: float      # 建議進場價（通常是觸發日收盤價，實際以次日開盤成交）
    stop_loss: float        # 停損價
    reason: str = ''        # 進場原因描述
    priority: float = 0.0   # 優先級（多訊號同時觸發時排序用）


# ============================================================
# Position — 單一持倉
# ============================================================
@dataclass
class Position:
    ticker: str
    direction: str          # 'long' or 'short'
    strategy: str
    entry_date: date
    entry_price: float      # 實際成交價（含滑點）
    shares: int
    stop_loss: float        # 初始停損價
    reason: str = ''

    # 追蹤用
    highest_since_entry: float = 0.0  # 進場後最高價（移動停利用）
    trailing_stop: float = 0.0        # 移動停損價
    cost_basis: float = 0.0           # 總成本（含手續費）
    days_held: int = 0

    def __post_init__(self):
        self.highest_since_entry = self.entry_price
        self.cost_basis = self.entry_price * self.shares

    @property
    def market_value(self):
        """以最近更新的價格計算市值（需外部呼叫 update 後才準確）"""
        return self._current_price * self.shares if hasattr(self, '_current_price') else self.cost_basis

    def update(self, current_price: float):
        """每日更新"""
        self._current_price = current_price
        self.days_held += 1
        if self.direction == 'long':
            self.highest_since_entry = max(self.highest_since_entry, current_price)
        else:
            self.highest_since_entry = min(self.highest_since_entry, current_price)

    def unrealized_pnl(self, current_price: float) -> float:
        if self.direction == 'long':
            return (current_price - self.entry_price) * self.shares
        else:
            return (self.entry_price - current_price) * self.shares

    def unrealized_pnl_pct(self, current_price: float) -> float:
        if self.entry_price == 0:
            return 0.0
        if self.direction == 'long':
            return (current_price / self.entry_price - 1) * 100
        else:
            return (1 - current_price / self.entry_price) * 100


# ============================================================
# Trade — 已平倉交易紀錄
# ============================================================
@dataclass
class Trade:
    ticker: str
    direction: str
    strategy: str
    entry_date: date
    entry_price: float
    exit_date: date
    exit_price: float
    shares: int
    pnl: float              # 淨盈虧（扣除手續費和滑點後）
    pnl_pct: float           # 盈虧百分比
    hold_days: int
    entry_reason: str
    exit_reason: str
    commission_total: float  # 進場+出場手續費

    @property
    def is_win(self) -> bool:
        return self.pnl > 0


# ============================================================
# Portfolio — 投資組合管理
# ============================================================
class Portfolio:
    def __init__(self, initial_capital: float = 100_000,
                 commission_per_trade: float = 1.0,
                 slippage_pct: float = 0.0005):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.commission_per_trade = commission_per_trade
        self.slippage_pct = slippage_pct

        self.positions: Dict[str, Position] = {}  # ticker -> Position
        self.closed_trades: List[Trade] = []
        self.equity_curve: List[dict] = []  # [{date, equity, cash, positions_value}]

        self._daily_pnl = 0.0
        self._today_date = None

    @property
    def positions_value(self) -> float:
        """所有持倉市值"""
        total = 0.0
        for pos in self.positions.values():
            if hasattr(pos, '_current_price'):
                total += pos._current_price * pos.shares
            else:
                total += pos.cost_basis
        return total

    @property
    def equity(self) -> float:
        """總淨值 = 現金 + 持倉市值"""
        return self.cash + self.positions_value

    @property
    def num_positions(self) -> int:
        return len(self.positions)

    def open_position(self, signal: Signal, shares: int,
                      actual_entry_price: float) -> Optional[Position]:
        """
        建倉。actual_entry_price 是次日開盤價（由外部提供）。
        回傳 Position 或 None（資金不足時）。
        """
        if signal.ticker in self.positions:
            return None  # 已有持倉

        # 計算含滑點的實際成交價
        if signal.direction == 'long':
            fill_price = actual_entry_price * (1 + self.slippage_pct)
        else:
            fill_price = actual_entry_price * (1 - self.slippage_pct)

        fill_price = round(fill_price, 2)
        cost = fill_price * shares + self.commission_per_trade

        if cost > self.cash:
            # 資金不足，縮減股數
            max_shares = int((self.cash - self.commission_per_trade) / fill_price)
            if max_shares <= 0:
                return None
            shares = max_shares
            cost = fill_price * shares + self.commission_per_trade

        self.cash -= cost

        pos = Position(
            ticker=signal.ticker,
            direction=signal.direction,
            strategy=signal.strategy,
            entry_date=signal.date,
            entry_price=fill_price,
            shares=shares,
            stop_loss=signal.stop_loss,
            reason=signal.reason,
        )
        pos.cost_basis = cost
        self.positions[signal.ticker] = pos
        return pos

    def close_position(self, ticker: str, exit_price: float,
                       exit_date: date, reason: str) -> Optional[Trade]:
        """平倉並記錄交易"""
        if ticker not in self.positions:
            return None

        pos = self.positions[ticker]

        # 含滑點的出場價
        if pos.direction == 'long':
            fill_price = exit_price * (1 - self.slippage_pct)
        else:
            fill_price = exit_price * (1 + self.slippage_pct)

        fill_price = round(fill_price, 2)
        proceeds = fill_price * pos.shares - self.commission_per_trade
        self.cash += proceeds

        # 計算盈虧
        total_commission = self.commission_per_trade * 2  # 進場 + 出場
        if pos.direction == 'long':
            gross_pnl = (fill_price - pos.entry_price) * pos.shares
        else:
            gross_pnl = (pos.entry_price - fill_price) * pos.shares

        net_pnl = gross_pnl - total_commission
        pnl_pct = (fill_price / pos.entry_price - 1) * 100 if pos.direction == 'long' \
            else (1 - fill_price / pos.entry_price) * 100

        trade = Trade(
            ticker=ticker,
            direction=pos.direction,
            strategy=pos.strategy,
            entry_date=pos.entry_date,
            entry_price=pos.entry_price,
            exit_date=exit_date,
            exit_price=fill_price,
            shares=pos.shares,
            pnl=round(net_pnl, 2),
            pnl_pct=round(pnl_pct, 2),
            hold_days=pos.days_held,
            entry_reason=pos.reason,
            exit_reason=reason,
            commission_total=total_commission,
        )
        self.closed_trades.append(trade)
        del self.positions[ticker]
        return trade

    def update_daily(self, current_date: date, prices: Dict[str, dict]):
        """
        每日收盤後更新所有持倉。
        prices: {ticker: {'Open': x, 'High': x, 'Low': x, 'Close': x}}
        """
        self._today_date = current_date

        for ticker in list(self.positions.keys()):
            if ticker in prices:
                bar = prices[ticker]
                self.positions[ticker].update(bar['Close'])

        # 記錄淨值
        self.equity_curve.append({
            'date': current_date.strftime('%Y-%m-%d'),
            'equity': round(self.equity, 2),
            'cash': round(self.cash, 2),
            'positions_value': round(self.positions_value, 2),
            'num_positions': self.num_positions,
        })

    def force_close_all(self, current_date: date, prices: Dict[str, dict]):
        """強制平倉所有持倉（回測結束時）"""
        for ticker in list(self.positions.keys()):
            if ticker in prices:
                self.close_position(ticker, prices[ticker]['Close'],
                                    current_date, '回測結束強制平倉')


# ============================================================
# RiskManager — 風險管理
# ============================================================
class RiskManager:
    def __init__(self,
                 max_risk_per_trade: float = 0.01,
                 max_positions: int = 6,
                 max_daily_loss_pct: float = 0.03,
                 max_sector_positions: int = 3):
        self.max_risk_per_trade = max_risk_per_trade
        self.max_positions = max_positions
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_sector_positions = max_sector_positions

                self._sector_map = {
            'semiconductor': ['NVDA', 'AMD', 'TSM', 'AVGO', 'ARM'],
            'optics_connect': ['COHR', 'LITE', 'CLS', 'FN', 'CAMT'],
            'servers_storage': ['SMCI', 'DELL', 'ANET', 'PSTG', 'WDC'],
            'cooling_infra': ['VRT', 'MOD', 'FIX', 'EME', 'JCI'],
            'power_grid': ['CEG', 'VST', 'GEV', 'ETN', 'SMR'],
            'cloud_software': ['PLTR', 'APP', 'MSFT', 'GOOGL', 'META'],
            'biotech_glp1': ['LLY', 'NVO', 'VKTX', 'TMDX', 'CRSP'],
            'space_defense': ['RKLB', 'LUNR', 'ASTS', 'GE', 'LMT'],
            'robotics_autonomous': ['TSLA', 'UBER', 'SYM', 'ISRG', 'ROK'],
            'cybersecurity_fintech': ['CRWD', 'PANW', 'NET', 'COIN', 'HOOD'],
        }
        # 反向對照
        self._ticker_sector = {}
        for sector, tickers in self._sector_map.items():
            for t in tickers:
                self._ticker_sector[t] = sector

    def calculate_position_size(self, portfolio: Portfolio,
                                entry_price: float,
                                stop_loss: float) -> int:
        """
        根據風險金額計算應買股數。
        公式: shares = (equity × max_risk) / |entry - stop|
        """
        risk_amount = portfolio.equity * self.max_risk_per_trade
        stop_distance = abs(entry_price - stop_loss)

        if stop_distance <= 0 or stop_distance < 0.01:
            # 停損距離太小，用保守的 3% 距離
            stop_distance = entry_price * 0.03

        shares = int(risk_amount / stop_distance)

        # 限制單筆不超過總資金的 20%
        max_by_equity = int(portfolio.equity * 0.20 / entry_price)
        shares = min(shares, max_by_equity)

        # 最少 1 股
        return max(shares, 1) if shares > 0 else 0

    def can_trade(self, portfolio: Portfolio, signal: Signal) -> Tuple[bool, str]:
        """
        進場前的所有風控檢查。
        回傳 (是否通過, 原因)。
        """
        # 1. 已有持倉
        if signal.ticker in portfolio.positions:
            return False, '已有持倉'

        # 2. 最大持倉數
        if portfolio.num_positions >= self.max_positions:
            return False, f'已達最大持倉數 {self.max_positions}'

        # 3. 現金不足
        min_cost = signal.entry_price * 1  # 至少買 1 股
        if portfolio.cash < min_cost + portfolio.commission_per_trade:
            return False, '現金不足'

        # 4. 板塊集中度
        ticker_sector = self._ticker_sector.get(signal.ticker, 'other')
        sector_count = sum(
            1 for t in portfolio.positions
            if self._ticker_sector.get(t, 'other') == ticker_sector
        )
        if sector_count >= self.max_sector_positions:
            return False, f'板塊 {ticker_sector} 已達最大 {self.max_sector_positions} 檔'

        # 5. 當日虧損檢查
        if len(portfolio.equity_curve) >= 2:
            today_eq = portfolio.equity
            prev_eq = portfolio.equity_curve[-1]['equity']
            daily_loss = (today_eq - prev_eq) / prev_eq
            if daily_loss < -self.max_daily_loss_pct:
                return False, f'當日虧損已達 {daily_loss*100:.1f}%，暫停交易'

        return True, 'OK'


# ============================================================
# PerformanceAnalyzer — 績效分析
# ============================================================
class PerformanceAnalyzer:
    def __init__(self, portfolio: Portfolio, risk_free_rate: float = 0.05):
        self.portfolio = portfolio
        self.risk_free_rate = risk_free_rate

    def analyze(self) -> dict:
        """計算所有績效指標"""
        trades = self.portfolio.closed_trades
        eq_curve = self.portfolio.equity_curve

        if not trades or not eq_curve:
            return {'error': '無交易紀錄或淨值曲線'}

        initial = self.portfolio.initial_capital
        final = eq_curve[-1]['equity']

        # --- 基本報酬 ---
        total_return_pct = (final / initial - 1) * 100
        trading_days = len(eq_curve)
        years = trading_days / 252
        cagr = ((final / initial) ** (1 / years) - 1) * 100 if years > 0 else 0

        # --- 淨值序列 ---
        equities = [e['equity'] for e in eq_curve]
        eq_series = pd.Series(equities)
        daily_returns = eq_series.pct_change().dropna()

        # --- Sharpe Ratio ---
        if len(daily_returns) > 1 and daily_returns.std() > 0:
            excess_return = daily_returns.mean() - self.risk_free_rate / 252
            sharpe = excess_return / daily_returns.std() * np.sqrt(252)
        else:
            sharpe = 0.0

        # --- Sortino Ratio ---
        downside = daily_returns[daily_returns < 0]
        if len(downside) > 1 and downside.std() > 0:
            sortino = (daily_returns.mean() - self.risk_free_rate / 252) / downside.std() * np.sqrt(252)
        else:
            sortino = 0.0

        # --- Max Drawdown ---
        peak = eq_series.expanding().max()
        drawdown = (eq_series - peak) / peak
        max_dd = drawdown.min() * 100
        max_dd_idx = drawdown.idxmin()

        # 回撤持續天數
        dd_duration = 0
        max_dd_duration = 0
        for i in range(len(drawdown)):
            if drawdown.iloc[i] < 0:
                dd_duration += 1
                max_dd_duration = max(max_dd_duration, dd_duration)
            else:
                dd_duration = 0

        # --- 交易統計 ---
        wins = [t for t in trades if t.is_win]
        losses = [t for t in trades if not t.is_win]

        win_rate = len(wins) / len(trades) * 100 if trades else 0
        avg_win = np.mean([t.pnl_pct for t in wins]) if wins else 0
        avg_loss = np.mean([t.pnl_pct for t in losses]) if losses else 0
        avg_hold = np.mean([t.hold_days for t in trades]) if trades else 0

        # Profit Factor
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        # Expectancy (per trade)
        expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)

        # Best / Worst trade
        best_trade = max(trades, key=lambda t: t.pnl_pct) if trades else None
        worst_trade = min(trades, key=lambda t: t.pnl_pct) if trades else None

        # Max consecutive wins/losses
        max_consec_wins, max_consec_losses = 0, 0
        consec_w, consec_l = 0, 0
        for t in trades:
            if t.is_win:
                consec_w += 1
                consec_l = 0
                max_consec_wins = max(max_consec_wins, consec_w)
            else:
                consec_l += 1
                consec_w = 0
                max_consec_losses = max(max_consec_losses, consec_l)

        # Total commission
        total_commission = sum(t.commission_total for t in trades)

        # --- 月度報酬 ---
        monthly_returns = self._calc_monthly_returns(eq_curve)

        # 正月份比例
        pos_months = sum(1 for m in monthly_returns if m['return_pct'] > 0)
        total_months = len(monthly_returns)
        monthly_win_rate = pos_months / total_months * 100 if total_months > 0 else 0

        # --- 策略歸因 ---
        strategy_breakdown = self._calc_strategy_breakdown(trades)

        # --- 組裝結果 ---
        result = {
            'summary': {
                'initial_capital': initial,
                'final_equity': round(final, 2),
                'total_return_pct': round(total_return_pct, 2),
                'cagr_pct': round(cagr, 2),
                'sharpe_ratio': round(sharpe, 2),
                'sortino_ratio': round(sortino, 2),
                'max_drawdown_pct': round(max_dd, 2),
                'max_drawdown_duration_days': max_dd_duration,
                'total_trades': len(trades),
                'win_rate_pct': round(win_rate, 1),
                'avg_win_pct': round(avg_win, 2),
                'avg_loss_pct': round(avg_loss, 2),
                'profit_factor': round(profit_factor, 2),
                'expectancy_pct': round(expectancy, 2),
                'avg_hold_days': round(avg_hold, 1),
                'max_consec_wins': max_consec_wins,
                'max_consec_losses': max_consec_losses,
                'gross_profit': round(gross_profit, 2),
                'gross_loss': round(gross_loss, 2),
                'total_commission': round(total_commission, 2),
                'best_trade': {
                    'ticker': best_trade.ticker,
                    'pnl_pct': best_trade.pnl_pct,
                    'strategy': best_trade.strategy,
                    'date': best_trade.entry_date.strftime('%Y-%m-%d'),
                } if best_trade else None,
                'worst_trade': {
                    'ticker': worst_trade.ticker,
                    'pnl_pct': worst_trade.pnl_pct,
                    'strategy': worst_trade.strategy,
                    'date': worst_trade.entry_date.strftime('%Y-%m-%d'),
                } if worst_trade else None,
                'monthly_win_rate_pct': round(monthly_win_rate, 1),
            },
            'equity_curve': eq_curve,
            'monthly_returns': monthly_returns,
            'strategy_breakdown': strategy_breakdown,
            'trades': [self._trade_to_dict(t) for t in trades],
        }
        return result

    def _calc_monthly_returns(self, eq_curve: list) -> list:
        """計算月度報酬"""
        if not eq_curve:
            return []

        monthly = {}
        for entry in eq_curve:
            month_key = entry['date'][:7]  # 'YYYY-MM'
            if month_key not in monthly:
                monthly[month_key] = {'first': entry['equity'], 'last': entry['equity']}
            monthly[month_key]['last'] = entry['equity']

        results = []
        for month, vals in sorted(monthly.items()):
            ret = (vals['last'] / vals['first'] - 1) * 100
            results.append({
                'month': month,
                'return_pct': round(ret, 2),
                'ending_equity': round(vals['last'], 2),
            })
        return results

    def _calc_strategy_breakdown(self, trades: list) -> dict:
        """按策略分組的績效歸因"""
        groups = {}
        for t in trades:
            if t.strategy not in groups:
                groups[t.strategy] = []
            groups[t.strategy].append(t)

        breakdown = {}
        for strategy, strades in groups.items():
            wins = [t for t in strades if t.is_win]
            losses = [t for t in strades if not t.is_win]
            gp = sum(t.pnl for t in wins)
            gl = abs(sum(t.pnl for t in losses))

            breakdown[strategy] = {
                'total_trades': len(strades),
                'wins': len(wins),
                'losses': len(losses),
                'win_rate_pct': round(len(wins) / len(strades) * 100, 1) if strades else 0,
                'total_pnl': round(sum(t.pnl for t in strades), 2),
                'avg_pnl_pct': round(np.mean([t.pnl_pct for t in strades]), 2) if strades else 0,
                'avg_win_pct': round(np.mean([t.pnl_pct for t in wins]), 2) if wins else 0,
                'avg_loss_pct': round(np.mean([t.pnl_pct for t in losses]), 2) if losses else 0,
                'profit_factor': round(gp / gl, 2) if gl > 0 else float('inf'),
                'avg_hold_days': round(np.mean([t.hold_days for t in strades]), 1) if strades else 0,
            }
        return breakdown

    def _trade_to_dict(self, t: Trade) -> dict:
        return {
            'ticker': t.ticker,
            'direction': t.direction,
            'strategy': t.strategy,
            'entry_date': t.entry_date.strftime('%Y-%m-%d'),
            'entry_price': t.entry_price,
            'exit_date': t.exit_date.strftime('%Y-%m-%d'),
            'exit_price': t.exit_price,
            'shares': t.shares,
            'pnl': t.pnl,
            'pnl_pct': t.pnl_pct,
            'hold_days': t.hold_days,
            'entry_reason': t.entry_reason,
            'exit_reason': t.exit_reason,
        }

    def print_report(self):
        """印出文字格式的績效報告"""
        r = self.analyze()
        if 'error' in r:
            print(f"⚠️ {r['error']}")
            return r

        s = r['summary']
        print("\n" + "=" * 70)
        print("  Anti-Gravity 回測績效報告")
        print("=" * 70)

        print(f"\n📊 總覽")
        print(f"  初始資金:        ${s['initial_capital']:>12,.2f}")
        print(f"  最終淨值:        ${s['final_equity']:>12,.2f}")
        print(f"  總報酬:          {s['total_return_pct']:>+11.2f}%")
        print(f"  年化報酬 (CAGR): {s['cagr_pct']:>+11.2f}%")

        print(f"\n📈 風險調整後指標")
        print(f"  Sharpe Ratio:    {s['sharpe_ratio']:>11.2f}  {'✅' if s['sharpe_ratio'] > 1.0 else '⚠️' if s['sharpe_ratio'] > 0.5 else '❌'}")
        print(f"  Sortino Ratio:   {s['sortino_ratio']:>11.2f}")
        print(f"  Max Drawdown:    {s['max_drawdown_pct']:>11.2f}%  {'✅' if s['max_drawdown_pct'] > -15 else '⚠️' if s['max_drawdown_pct'] > -25 else '❌'}")
        print(f"  MDD 持續天數:    {s['max_drawdown_duration_days']:>11d} 天")

        print(f"\n🎯 交易統計")
        print(f"  總交易數:        {s['total_trades']:>11d}")
        print(f"  勝率:            {s['win_rate_pct']:>11.1f}%  {'✅' if s['win_rate_pct'] > 50 else '⚠️'}")
        print(f"  平均獲利:        {s['avg_win_pct']:>+11.2f}%")
        print(f"  平均虧損:        {s['avg_loss_pct']:>+11.2f}%")
        print(f"  Profit Factor:   {s['profit_factor']:>11.2f}  {'✅' if s['profit_factor'] > 1.5 else '⚠️' if s['profit_factor'] > 1.0 else '❌'}")
        print(f"  Expectancy:      {s['expectancy_pct']:>+11.2f}%/筆")
        print(f"  平均持有天數:    {s['avg_hold_days']:>11.1f}")
        print(f"  最大連勝:        {s['max_consec_wins']:>11d}")
        print(f"  最大連敗:        {s['max_consec_losses']:>11d}")
        print(f"  總手續費:        ${s['total_commission']:>11.2f}")

        if s['best_trade']:
            bt = s['best_trade']
            print(f"\n  🏆 最佳交易: {bt['ticker']} ({bt['strategy']}) {bt['date']} → {bt['pnl_pct']:+.2f}%")
        if s['worst_trade']:
            wt = s['worst_trade']
            print(f"  💀 最差交易: {wt['ticker']} ({wt['strategy']}) {wt['date']} → {wt['pnl_pct']:+.2f}%")

        print(f"\n📋 策略歸因")
        print(f"  {'策略':<25} {'交易數':>6} {'勝率':>8} {'P/L':>10} {'PF':>6} {'平均持有':>8}")
        print(f"  {'-'*25} {'-'*6} {'-'*8} {'-'*10} {'-'*6} {'-'*8}")
        for strat, data in r['strategy_breakdown'].items():
            pf_str = f"{data['profit_factor']:.2f}" if data['profit_factor'] != float('inf') else '∞'
            print(f"  {strat:<25} {data['total_trades']:>6} {data['win_rate_pct']:>7.1f}% ${data['total_pnl']:>9,.2f} {pf_str:>6} {data['avg_hold_days']:>7.1f}d")

        print(f"\n📅 月度報酬")
        for m in r['monthly_returns']:
            bar_len = int(abs(m['return_pct']) * 2)
            bar = '█' * min(bar_len, 30)
            color = '🟢' if m['return_pct'] > 0 else '🔴' if m['return_pct'] < 0 else '⚪'
            print(f"  {m['month']}  {color} {m['return_pct']:>+7.2f}%  {bar}")
        print(f"  月度勝率: {s['monthly_win_rate_pct']:.1f}%")

        print("\n" + "=" * 70)
        return r
