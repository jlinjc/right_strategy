"""
generate_signals.py - 定案策略即時信號產生器
================================================
用「完全相同的定案策略物件」掃描追蹤清單，輸出今日可執行的進場信號卡 +
大盤 regime 燈號 + 設定中觀察清單，寫入 Web_Dashboard/strategy_signals.json
給 strategy_dashboard.html 使用。

定案系統：
  進場 = MA拉回 + TTM動能>0 + 不追高(<1.08×10MA)
  出場 = 分批(進場+3×ATR賣50% + 剩餘吊燈3.5×ATR)，硬停損 = 進場時 ATR/均線停損
"""

import os
import sys
import json
import warnings
import urllib.request
from io import StringIO
from datetime import datetime

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import yfinance as yf

from scanner_base import AI_TECH_STOCKS, BENCHMARK, DASHBOARD_DIR
from filter_experiments import f_mom_positive, make_f_not_extended, _ttm_mom
from exit_experiments import _atr_at
from rs_selection import compute_rs_rank, RSRankScaledExit
from validate_universe import DIVERSE_UNIVERSE
from earnings_filter import fetch_earnings, earnings_context, BLACKOUT_DAYS
from sector_rs import (fetch_sector_map, compute_sector_rs, sector_context,
                       SECTOR_RS_FLOOR)
from signal_quality import (pullback_quality, extension_state, conviction,
                            portfolio_heat, compute_ext_rank,
                            regime_risk_mult, allocate_capacity)

# 進場端參數
SCALE_ATR_MULT = 3.0   # 仍用於顯示「第一參考目標」(非出場指令)
# ── 出場：結構騎乘(2026-06 優化定案) ──
# 回測(optimize_trend_exit.py，含2022完整週期)證實：把「+3ATR砍半+ATR吊燈」換成
# 「不早砍、全倉騎、收盤跌破50MA才走」→ 總報酬 +86%→+148%(NDX)。AI 強者恆強時
# 不被波動洗出、讓右尾跑到趨勢真正破壞才出。不落袋(任何提早落袋都砍右尾)、
# 移除時間停損、保留災難硬停損 + regime 空頭出場。
TRAIL_MA = 50          # 主結構出場：收盤跌破 50MA 才走（最大財富設定）
TRAIL_MA_TIGHT = 20    # 較保守替代線：收盤跌破 20MA（較會躲熊、少賺一點）
ACCOUNT_SIZE = 100_000
RISK_PER_TRADE = 0.01          # 基準單筆風險（信心分級會在 0.6%~1.4% 間調整）
RS_THRESHOLD = 80.0   # 只交易 RS 排名前 20% 的領導股（系統化選股，洞#1 解法）
MAX_PORTFOLIO_HEAT = 3.0       # 組合總風險上限(%)，與回測 RiskManager 一致
MAX_SECTOR_POSITIONS = 3       # 同板塊持倉上限

# ── live 風險治理（純 live overlay，不動回測路徑）──
MAX_GROSS_EXPOSURE = 150.0     # gross 名目曝險上限(%)：防高相關 book 群聚跳空
# 名目上限隨信心分級（修正「20%名目吞掉風險制、conviction 靜默失效」）
TIER_NOTIONAL = {'A': 0.20, 'B': 0.15, 'C': 0.10}
ENTRY_CHASE_CAP = 0.03         # 進場限價上限：次日 open 最多追到 close×1.03，超過不追
# 停損距離地板：均線停損常落在 <1%（噪音內）→ live 實際停損不窄於 1.5×ATR
# (= 策略自身 stop_by_atr=close-1.5ATR)，只加寬噪音級停損、永不收緊原本較寬的停損。
# 邏輯依據(非回測背書)：停損在 1×ATR 內者被日內噪音+次日open跳空洗出的機率主導報酬。
STOP_FLOOR_ATR_MULT = 1.5

# 退回用手挑廣股池（僅當抓不到 S&P500 成分時的 fallback）
FALLBACK_UNIVERSE = sorted(set(AI_TECH_STOCKS) | set(DIVERSE_UNIVERSE))
SP500_CACHE = os.path.join(DASHBOARD_DIR, 'sp500_universe.json')


def get_universe():
    """系統化選股池 = S&P500 全成分（維基，快取到 Web_Dashboard）。
    回測(validate_pit_universe.py)證實：『廣池 + 系統化 RS 前20% 選股』誠實尺
    Sharpe≈+1.14，與舊手挑 109 檔(+1.21)相當，但**完全無後照鏡、可辯護、投資人可信**。
    這是把『選誰』交給規則、而非人工挑贏家名單 —— 策略可重複性的根。
    抓取失敗 → 用快取 → 再退回手挑廣池，確保 live 不中斷。"""
    try:
        url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode('utf-8')
        syms = sorted(set(pd.read_html(StringIO(html))[0]['Symbol'].astype(str)
                          .str.replace('.', '-', regex=False).tolist()))
        if len(syms) >= 400:
            with open(SP500_CACHE, 'w', encoding='utf-8') as f:
                json.dump(syms, f)
            return syms
    except Exception as e:
        print(f"⚠️ 抓 S&P500 成分失敗({e})，改用快取/退回手挑廣池")
    if os.path.exists(SP500_CACHE):
        try:
            with open(SP500_CACHE, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return FALLBACK_UNIVERSE


def download_universe(tickers, period='2y', batch=100):
    """分批批量下載價格（避免單次過大）。回傳 (bm_df, {ticker: df})。
    只下載價格(快)；昂貴的 .info(板塊/財報) 留待只對觸發候選抓取。"""
    stocks, bm = {}, None
    all_t = [BENCHMARK] + list(tickers)
    for i in range(0, len(all_t), batch):
        chunk = all_t[i:i + batch]
        raw = yf.download(' '.join(chunk), period=period, interval='1d',
                          progress=False, group_by='ticker')
        for tk in chunk:
            try:
                df = raw[tk].dropna(how='all').copy()
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(-1)
                if tk == BENCHMARK:
                    bm = df
                elif len(df) >= 260:
                    stocks[tk] = df
            except Exception:
                pass
        print(f"   ...下載 {min(i + batch, len(all_t))}/{len(all_t)}")
    return bm, stocks


def compute_regime(bm: pd.DataFrame) -> dict:
    """大盤 QQQ regime 燈號 —— 人工看大盤手動控倉的紀律工具"""
    close = bm['Close']
    last = float(close.iloc[-1])
    ema21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])
    ma20 = float(close.iloc[-20:].mean())
    ma50 = float(close.iloc[-50:].mean())
    ma200 = float(close.iloc[-200:].mean()) if len(close) >= 200 else ma50

    if last >= ema21 and last >= ma50:
        light, label, action = ('green', '🟢 多頭 (QQQ > 21EMA 且 > 50MA)',
                                '可滿倉執行信號，正常控倉')
    elif last >= ma50:
        light, label, action = ('yellow', '🟡 震盪 (QQQ < 21EMA 但 > 50MA)',
                                '減半部位，只接最強信號')
    else:
        light, label, action = ('red', '🔴 修正 (QQQ < 50MA)',
                                '策略歷史上修正期失血 —— 停手或極輕倉觀望')
    return {
        'light': light, 'label': label, 'action': action,
        'qqq_close': round(last, 2), 'ema21': round(ema21, 2),
        'ma20': round(ma20, 2), 'ma50': round(ma50, 2), 'ma200': round(ma200, 2),
        'above_ema21': bool(last >= ema21), 'above_ma50': bool(last >= ma50),
    }


def build_card(tk: str, df: pd.DataFrame, sig, rs_pct=None,
               earn_ctx=None, sec_ctx=None, ext_rank_pct=None,
               heat_info=None, regime_light='green') -> dict:
    """把一個進場訊號組成可執行的交易卡（含分批目標/信心分級部位/容量旗標）。
    本函式只算「想要的部位 _desired_shares」(風險制 × regime × 信心分級名目上限)，
    真正的熱度/gross/板塊縮倉交給 allocate_capacity 依信心序統一分配。"""
    idx = len(df) - 1
    entry = float(sig.entry_price)
    atr = _atr_at(df, idx)
    # ── #2 停損加寬：均線停損若落在 <1.5×ATR(噪音內)，加寬到 1.5×ATR 地板 ──
    # min() 只取較寬(較低)者 → 永不收緊原本已較寬的停損
    raw_stop = float(sig.stop_loss)
    floor_stop = entry - STOP_FLOOR_ATR_MULT * atr
    stop = round(min(raw_stop, floor_stop), 2)
    stop_floored = stop < raw_stop - 1e-9
    ref_target = entry + SCALE_ATR_MULT * atr   # 僅供參考的「初步漲幅感」，非出場指令
    risk_per_share = max(entry - stop, 0.01)
    risk_pct = risk_per_share / entry * 100
    mom = _ttm_mom(idx, df)
    ma10 = float(df['Close'].iloc[-10:].mean())
    # ── 結構騎乘出場線（收盤跌破才走；隨股價上行而上移）──
    trail_line = float(df['Close'].iloc[-TRAIL_MA:].mean())        # 主出場線 50MA
    trail_line_tight = float(df['Close'].iloc[-TRAIL_MA_TIGHT:].mean())  # 保守線 20MA
    earn_ctx = earn_ctx or {}
    sec_ctx = sec_ctx or {}
    heat_info = heat_info or {'open_heat_pct': 0.0, 'sector_counts': {}}

    # ── #3 拉回品質 + 趨勢延伸 + 信心分級下注 ──
    pq = pullback_quality(df, idx)
    ext = extension_state(df, idx, ext_rank_pct)
    conv = conviction(rs_pct, sec_ctx.get('sector_rs'), earn_ctx.get('pead_active'),
                      pq['score'], ext['late_stage'],
                      base_risk=RISK_PER_TRADE)

    # ── 大盤 regime 縮倉（自動化原本人工的減倉紀律）──
    reg_mult = regime_risk_mult(regime_light)
    target_risk_pct = conv['eff_risk_pct'] / 100.0 * reg_mult   # 實際目標風險(占帳戶)

    # 想要的部位 = 風險制；上限改為「隨信心分級的名目上限」
    # (修正：固定 20% 名目會吞掉風險制、讓 A/B/C 分級在緊停損時靜默失效)
    notional_cap = TIER_NOTIONAL.get(conv['tier'], 0.10)
    desired = int((ACCOUNT_SIZE * target_risk_pct) / risk_per_share)
    desired = min(desired, int(ACCOUNT_SIZE * notional_cap / entry))
    desired = max(desired, 0)

    # ── #4 進場追價上限：次日 open 成交，最多追到 close×(1+chase)，超過放棄 ──
    entry_limit = round(entry * (1 + ENTRY_CHASE_CAP), 2)

    sec = sec_ctx.get('sector') or 'other'
    sector_held = heat_info.get('sector_counts', {}).get(sec, 0)

    return {
        'ticker': tk,
        'entry': round(entry, 2),
        'entry_limit': entry_limit,
        'stop': stop,
        'stop_raw': round(raw_stop, 2),       # 策略原始停損（加寬前）
        'stop_floored': bool(stop_floored),   # 是否已加寬到 1.5×ATR 地板
        'risk_pct': round(risk_pct, 2),
        # ── 出場：結構騎乘（不早砍、收盤跌破均線才走；線會隨股價上移）──
        'exit_rule': f'騎乘：收盤跌破{TRAIL_MA}MA才出，期間抱住讓贏家跑',
        'trail_ma': TRAIL_MA,
        'trail_line': round(trail_line, 2),            # 目前 50MA 出場線
        'trail_ma_tight': TRAIL_MA_TIGHT,
        'trail_line_tight': round(trail_line_tight, 2),  # 目前 20MA 保守線
        'ref_target': round(ref_target, 2),            # 參考漲幅(非指令)
        'ref_target_gain_pct': round((ref_target / entry - 1) * 100, 1),
        'atr': round(atr, 2),
        # 想要的部位（容量分配前），allocate_capacity 會夾成最終 suggested_shares
        '_desired_shares': desired,
        'suggested_shares': desired,
        'suggested_dollars': round(desired * entry, 0),
        'regime_mult': reg_mult,
        'momentum': round(mom, 2),
        'rs_pct': round(rs_pct, 0) if rs_pct is not None else None,
        'ext_vs_10ma_pct': round((entry / ma10 - 1) * 100, 1),
        'reason': sig.reason,
        # ── #1 財報情境 ──
        'days_to_earnings': earn_ctx.get('days_to_earnings'),
        'days_since_earnings': earn_ctx.get('days_since_earnings'),
        'last_surprise_pct': earn_ctx.get('last_surprise_pct'),
        'pead_active': bool(earn_ctx.get('pead_active')),
        # ── #2 板塊相對強度 ──
        'sector': sec_ctx.get('sector'),
        'sector_rs': sec_ctx.get('sector_rs'),
        # ── #3 訊號品質 + 信心分級下注 ──
        'pullback_quality': pq['score'],
        'pq_vol_contraction_pct': pq['vol_contraction_pct'],
        'pq_waterfall': pq['waterfall'],
        'ext_above_200ma_pct': ext['ext_above_200ma_pct'],
        'ext_self_pct': ext['ext_self_pct'],
        'late_stage': ext['late_stage'],
        'conviction_score': conv['score'],
        'conviction_tier': conv['tier'],
        'eff_risk_pct': conv['eff_risk_pct'],          # 信心分級目標(未含 regime)
        'target_risk_pct': round(target_risk_pct * 100, 2),  # 含 regime 後目標風險
        'notional_cap_pct': int(notional_cap * 100),
        # trade_risk_pct / capacity_capped / no_room / capacity_note 由 allocate_capacity 寫入
        # ── #4 組合熱度 / 板塊（實際縮倉由 allocate_capacity 執行）──
        'portfolio_heat_pct': heat_info.get('open_heat_pct', 0.0),
        'portfolio_gross_pct': heat_info.get('open_gross_pct', 0.0),
        'sector_held': sector_held,
    }


def build_watch(tk: str, df: pd.DataFrame) -> dict:
    """非觸發股的設定狀態：離最近拉回均線多遠 + 動能/趨勢，看誰在醞釀"""
    idx = len(df) - 1
    close = float(df['Close'].iloc[-1])
    ma10 = float(df['Close'].iloc[-10:].mean())
    ma20 = float(df['Close'].iloc[-20:].mean())
    ma50 = float(df['Close'].iloc[-50:].mean())
    ma200 = float(df['Close'].iloc[-200:].mean()) if len(df) >= 200 else ma50
    mom = _ttm_mom(idx, df)
    # 離最近的下方拉回均線距離（正=均線在下方，越小越接近拉回）
    dists = []
    for w, v in (('10MA', ma10), ('20MA', ma20), ('60MA', float(df['Close'].iloc[-60:].mean()))):
        dists.append((w, (close / v - 1) * 100))
    nearest = min(dists, key=lambda x: abs(x[1]))
    return {
        'ticker': tk,
        'close': round(close, 2),
        'above_200ma': bool(close >= ma200),
        'momentum_pos': bool(mom > 0),
        'nearest_ma': nearest[0],
        'nearest_ma_dist_pct': round(nearest[1], 1),
        'trend_ok': bool(close >= ma200 and ma10 >= ma20),
    }


def main():
    universe = get_universe()
    src = 'S&P500 系統化廣池' if universe is not FALLBACK_UNIVERSE else '手挑廣池(fallback)'
    print(f"📥 下載 {src} {len(universe)} 檔 + {BENCHMARK} (2y，分批)...")
    bm, stocks = download_universe(universe, period='2y')
    if bm is None or not stocks:
        print('❌ 資料下載失敗，中止。'); return
    if isinstance(bm.columns, pd.MultiIndex):
        bm.columns = bm.columns.get_level_values(-1)

    regime = compute_regime(bm)

    # 橫斷面 RS 排名（在整個廣池內 point-in-time 排名 → 系統化選領導股）
    rs_rank = compute_rs_rank(stocks)
    last_ts = bm.index[-1]
    rs_today = rs_rank.get(last_ts, {})
    data_date = last_ts.date() if hasattr(last_ts, 'date') else last_ts
    print(f"📊 {len(stocks)} 檔有效 | 板塊相對強度 (11 SPDR ETF voladj)...")
    sector_rs = compute_sector_rs()
    ext_rank = compute_ext_rank(stocks)

    # 讀 live 持倉
    positions = []
    ppath = os.path.join(DASHBOARD_DIR, 'positions.json')
    if os.path.exists(ppath):
        try:
            with open(ppath, encoding='utf-8') as f:
                pj = json.load(f)
            positions = pj if isinstance(pj, list) else pj.get('positions', [])
        except Exception:
            positions = []
    held_tks = [p.get('ticker') for p in positions if p.get('ticker')]

    # 定案策略：廣股池 + RS 前 20% 選股（此物件只用於「進場掃描」；
    # live 出場已改為結構騎乘，下列出場參數對 .scan() 無作用）
    # not_ext 由 1.08 放寬到 1.20：與「讓贏家跑」一致，不在強勢拉回上過早拒絕進場
    strategy = RSRankScaledExit(
        rs_rank=rs_rank, rs_threshold=RS_THRESHOLD,
        filters=[('mom+', f_mom_positive), ('not_ext', make_f_not_extended(1.20))],
        atr_target_mult=SCALE_ATR_MULT,
    )

    # ── 第一遍掃描：只用價格+RS 找出「觸發候選」(便宜) ──
    triggered, watch = [], []
    for tk, df in stocks.items():
        idx = len(df) - 1
        sig = strategy.scan(idx, tk, df, bm)
        if sig is not None:
            triggered.append((tk, df, sig))
        else:
            w = build_watch(tk, df)
            w['rs_pct'] = round(rs_today.get(tk), 0) if tk in rs_today else None
            if w['trend_ok'] and w['momentum_pos'] and (w['rs_pct'] or 0) >= RS_THRESHOLD:
                watch.append(w)

    # ── 只對「觸發候選 ∪ 持倉」抓昂貴的 .info（財報/板塊）→ 廣池可行的關鍵 ──
    info_tks = sorted(set(t[0] for t in triggered) | set(held_tks))
    print(f"🔎 觸發候選 {len(triggered)} 檔，只對候選+持倉共 {len(info_tks)} 檔抓財報/板塊...")
    earnings = fetch_earnings(info_tks) if info_tks else {}
    sector_map = fetch_sector_map(info_tks, use_cache=True) if info_tks else {}

    # #4 組合熱度（持倉板塊已在 info_tks 內）
    heat_info = portfolio_heat(positions, sector_map, ACCOUNT_SIZE)
    print(f"📊 現有持倉 {len(heat_info['held_tickers'])} 檔 | "
          f"組合熱度 {heat_info['open_heat_pct']:.2f}% / gross {heat_info.get('open_gross_pct',0):.0f}% / "
          f"上限 {MAX_PORTFOLIO_HEAT:.0f}%")

    # 載入當下基本面（顯示用，非硬濾網 —— 回測證明硬篩無益）
    fund = {}
    fpath = os.path.join(DASHBOARD_DIR, 'broad_fundamentals.json')
    if os.path.exists(fpath):
        try:
            with open(fpath, encoding='utf-8') as f:
                fund = json.load(f)
        except Exception:
            fund = {}

    def fund_of(tk):
        d = fund.get(tk, {})
        if not d:
            return None
        def pct(x):
            return round(x * 100, 1) if isinstance(x, (int, float)) else None
        return {
            'net_margin': pct(d.get('profitMargins')),
            'roe': pct(d.get('returnOnEquity')),
            'rev_growth': pct(d.get('revenueGrowth')),
            'earn_growth': pct(d.get('earningsGrowth')),
            'fwd_pe': round(d['forwardPE'], 1) if isinstance(d.get('forwardPE'), (int, float)) else None,
        }

    # ── 第二遍：只對觸發候選做財報/板塊硬擋 + 組卡 ──
    cards, filtered = [], []
    for tk, df, sig in triggered:
        earn_ctx = earnings_context(earnings, tk, data_date)
        sec_ctx = sector_context(sector_map, sector_rs, tk)
        # ── #1 財報盲區：硬擋（撞財報前夕的拉回是擲骰子，不是 edge）──
        if earn_ctx['in_blackout']:
            filtered.append({'ticker': tk, 'reason': 'earnings_blackout',
                             'detail': f"財報前 {earn_ctx['days_to_earnings']} 天",
                             'sector_rs': sec_ctx['sector_rs']})
            continue
        # ── #2 落後板塊：硬擋（落隊板塊裡的假強股，拉回失敗率高）──
        if sec_ctx['sector_lagging']:
            filtered.append({'ticker': tk, 'reason': 'sector_lagging',
                             'detail': f"{sec_ctx['sector']} RS={sec_ctx['sector_rs']:.0f}",
                             'sector_rs': sec_ctx['sector_rs']})
            continue
        c = build_card(tk, df, sig, rs_pct=rs_today.get(tk),
                       earn_ctx=earn_ctx, sec_ctx=sec_ctx,
                       ext_rank_pct=ext_rank.get(tk), heat_info=heat_info,
                       regime_light=regime['light'])
        c['fundamentals'] = fund_of(tk)
        cards.append(c)

    # 排序：信心分數高的排前面（綜合 RS/板塊/拉回品質/PEAD/末段）→ 風險小
    cards.sort(key=lambda c: (-(c['conviction_score'] or 0), c['risk_pct']))
    watch.sort(key=lambda w: abs(w['nearest_ma_dist_pct']))

    # ── 容量分配：依信心序統一吃「組合熱度 / gross 名目 / 板塊席次」容量 ──
    # 最佳信號先吃，後續被剩餘容量夾小/歸零 → 真正縮倉而非旗標（#7 群聚防爆倉）
    allocate_capacity(
        cards,
        open_heat_pct=heat_info.get('open_heat_pct', 0.0),
        open_gross_pct=heat_info.get('open_gross_pct', 0.0),
        open_sector_counts=heat_info.get('sector_counts', {}),
        account_size=ACCOUNT_SIZE,
        max_heat_pct=MAX_PORTFOLIO_HEAT,
        max_gross_pct=MAX_GROSS_EXPOSURE,
        max_sector_positions=MAX_SECTOR_POSITIONS,
    )

    out = {
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'data_date': last_ts.strftime('%Y-%m-%d') if hasattr(last_ts, 'strftime') else str(last_ts),
        'regime': regime,
        'config': {
            'universe': f'廣股池 {len(stocks)} 檔，RS 排名前 {int(100-RS_THRESHOLD)}% 才入選',
            'entry': (f'RS前20% + MA拉回 + TTM動能>0 + 不追高(<1.20×10MA) '
                      f'+ 財報盲區(前{BLACKOUT_DAYS}天禁入) + 板塊RS閘門'
                      f'(<{SECTOR_RS_FLOOR:.0f}落後板塊不接)'),
            'exit': (f'結構騎乘：不早砍、抱住讓贏家跑，收盤跌破{TRAIL_MA}MA才出'
                     f'(保守可用{TRAIL_MA_TIGHT}MA)；保留災難硬停損；移除時間停損'),
            'sizing': (f'信心分級下注：A級×1.35 / B×1.0 / C×0.7，錨定{RISK_PER_TRADE*100:.0f}%，'
                       f'封頂[0.6%,1.4%]；名目上限 A{int(TIER_NOTIONAL["A"]*100)}/'
                       f'B{int(TIER_NOTIONAL["B"]*100)}/C{int(TIER_NOTIONAL["C"]*100)}%；'
                       f'regime 縮倉 綠1.0/黃0.5/紅0.25'),
            'risk_governance': (f'組合熱度≤{MAX_PORTFOLIO_HEAT:.0f}% + gross名目≤{MAX_GROSS_EXPOSURE:.0f}% '
                                f'+ 同板塊≤{MAX_SECTOR_POSITIONS}檔，依信心序實際縮倉；'
                                f'進場追價上限 close×{1+ENTRY_CHASE_CAP:.2f}'),
            'account_size': ACCOUNT_SIZE, 'risk_per_trade_pct': RISK_PER_TRADE * 100,
            'blackout_days': BLACKOUT_DAYS, 'sector_rs_floor': SECTOR_RS_FLOOR,
            'max_portfolio_heat_pct': MAX_PORTFOLIO_HEAT,
            'max_gross_exposure_pct': MAX_GROSS_EXPOSURE,
            'tier_notional': TIER_NOTIONAL, 'entry_chase_cap': ENTRY_CHASE_CAP,
            'regime_size_mult': {'green': 1.0, 'yellow': 0.5, 'red': 0.25},
        },
        'portfolio_heat': heat_info,
        'sector_rs': sector_rs,
        'signals': cards,
        'watchlist': watch,
        'filtered': filtered,
        'n_signals': len(cards),
        'n_watch': len(watch),
        'n_filtered': len(filtered),
    }
    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    path = os.path.join(DASHBOARD_DIR, 'strategy_signals.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"✅ {regime['label']}")
    print(f"✅ {len(cards)} 個進場信號 / {len(watch)} 個醞釀中 / "
          f"{len(filtered)} 個被財報·板塊閘門擋下 (廣股池 {len(stocks)} 檔)")
    for c in cards:
        tags = []
        if c['pead_active']:
            tags.append(f"PEAD+{c['last_surprise_pct']:.0f}%")
        tags.append(f"PQ{c['pullback_quality']:.0f}")
        if c['sector_rs'] is not None:
            tags.append(f"{c['sector']}RS{c['sector_rs']:.0f}")
        if c['late_stage']:
            tags.append("末段")
        if c.get('stop_floored'):
            tags.append(f"停損加寬→${c['stop']:.2f}")
        warns = []
        if c.get('no_room'):
            warns.append(f"⛔無容量({c.get('capacity_note') or ''})")
        elif c.get('capacity_capped'):
            warns.append(f"⚠️縮倉({c.get('capacity_note') or ''})")
        tagstr = '  [' + ' | '.join(tags) + ']'
        warnstr = ('  ' + ' '.join(warns)) if warns else ''
        regstr = '' if c.get('regime_mult', 1.0) == 1.0 else f"×{c['regime_mult']}"
        print(f"   {c['conviction_tier']}({c['conviction_score']:.0f}) {c['ticker']:6} "
              f"RS{c['rs_pct']:.0f} 進${c['entry']:.2f}(限${c['entry_limit']:.2f}) 停${c['stop']:.2f} "
              f"風險{c['trade_risk_pct']:.2f}%{regstr}({c['suggested_shares']}股) "
              f"→ 騎{c['trail_ma']}MA出場線${c['trail_line']:.2f}{tagstr}{warnstr}")
    if filtered:
        print(f"  🚫 閘門擋下：")
        for fdict in filtered:
            print(f"     {fdict['ticker']:6} {fdict['reason']:18} {fdict['detail']}")
    print(f"💾 {path}")


if __name__ == '__main__':
    main()
