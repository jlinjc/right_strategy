"""
taiwan_status.py - 台股核心(擇時ETF)狀態產生器(美股 core_status.py 的台股孿生版)
========================================================================
把美股定案引擎完整搬到台股:每檔趨勢ETF用自己的進出場+RiskTarget下注,月輪動持最強,
全球信用+費半哨(台股最佳正交領先哨=HYG+LQD+SMH,research_taiwan_core.py 實證),
恐慌容忍(全球^VIX)。最低持有30日鎖(同美股)。

★ 核心宇宙(只收『趨勢載具』,排除高股息均值回歸=回測證實擇時傷):
  0050.TW元大台灣50 / 0052.TW富邦科技(台股科技動能=最強) / 006208.TW富邦台50 /
  0051.TW元大中型100 / 00757.TW統一FANG+(美科技台幣計價)
  + live候選(歷史不足無法回測,滿200日MA才納入):00935野村新科技50 / 00891中信半導體 等

★ 台股最佳信用哨(research_taiwan_core.py 對決):HYG+LQD+SMH 三合(各看是否站上自己200MA),
  健康比例×曝險。台股=全球半導體供應鏈,費半(SMH)領先;本地USDTWD無效(Sharpe僅1.03)。

★ .TW 資料清理:yfinance 台股有假分割/尖刺(如0050.TW 2014、0052.TW 2025)→splice接回,
  否則 live MA200 會被污染。報酬序列連續即可信(絕對價位 yfinance 可能標錯,不影響擇時)。

輸出 Web_Dashboard/taiwan_status.json。用法: python taiwan_status.py
"""
import os, sys, json, warnings
from datetime import datetime
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import yfinance as yf
from scanner_base import DASHBOARD_DIR

# 可回測核心(趨勢載具) + per-index 參數(research_taiwan_core.py + research_taiwan_optimize.py 校準)
#   per-ticker thr/buf:00757(美科技,波動大)放寬1.12/1.00、0050貼1.06/0.98(grid實益最大),其餘統一1.08/0.99。
TW_PARAMS = {
    '0050.TW':   {'name': '元大台灣50',  'entry_thr': 1.06, 'exit_buf': 0.98, 'budget': 0.0863, 'cap': 1.5},
    '0052.TW':   {'name': '富邦科技',    'entry_thr': 1.08, 'exit_buf': 0.99, 'budget': 0.0936, 'cap': 1.5},
    '006208.TW': {'name': '富邦台50',    'entry_thr': 1.08, 'exit_buf': 0.99, 'budget': 0.0807, 'cap': 1.5},
    '0051.TW':   {'name': '元大中型100', 'entry_thr': 1.08, 'exit_buf': 0.99, 'budget': 0.0787, 'cap': 1.5},
    '00757.TW':  {'name': '統一FANG+',   'entry_thr': 1.12, 'exit_buf': 1.00, 'budget': 0.1276, 'cap': 1.5},
}
# live-only 候選(歷史不足,有≥200日資料才顯示、且標註不可回測)
TW_WATCH = {
    '00935.TW':  '野村臺灣新科技50',
    '00891.TW':  '中信關鍵半導體',
    '00892.TW':  '富邦台灣半導體',
    '00981A.TW': '主動統一台股增長',
}
CORE_TICKERS = list(TW_PARAMS)
# ★台股最佳哨(research_taiwan_optimize.py 對決):HYG+LQD+SOXX(全球信用+費半SOXX)。
#   SOXX費半單獨 Sharpe1.99>SMH1.93;HYG+LQD+SOXX三合 OOS最差1.40最穩(信用補費半在利率型回撤的盲點)。
CANARY_TICKERS = ['HYG', 'LQD', 'SOXX']
CANARY_NAME = {'HYG': '美高收益債', 'LQD': '美投資級債', 'SOXX': '費半SOXX'}
PANIC_TICKER = '^VIX'
MA, MA_FAST = 200, 50
PANIC_VIX, PANIC_DELAY = 30.0, 3
RISK_MULT = 1.0
RISK_MULT = min(RISK_MULT, 1.5)
STOP_DIST_FLOOR = 0.02
MIN_HOLD_DAYS = 30          # 路線B最低持有(≈21交易日,同美股 research_rotation_manual.py)
DEFAULT_PARAM = {'name': '?', 'entry_thr': 1.08, 'exit_buf': 0.99, 'budget': 0.09, 'cap': 1.5}


def clean(s: pd.Series) -> pd.Series:
    """清 .TW yfinance 假分割/尖刺(splice接回),否則 MA200 被污染。報酬序列連續即可信。"""
    v = s.dropna().astype(float).values.copy(); idx = s.dropna().index
    for i in range(1, len(v)):
        if v[i-1] <= 0 or v[i] <= 0: continue
        ch = v[i] / v[i-1] - 1
        if abs(ch) > 0.35:
            spike = False
            if i+1 < len(v) and v[i] > 0:
                nx = v[i+1] / v[i] - 1
                if abs(nx) > 0.30 and (nx > 0) != (ch > 0): spike = True
            if spike:
                v[i] = (v[i-1] + v[i+1]) / 2.0
            else:
                v[i:] = v[i:] * (v[i-1] / v[i])
    return pd.Series(v, index=idx)


def core_signal(close: pd.Series, vix_last, ticker: str, name: str) -> dict:
    p = TW_PARAMS.get(ticker, dict(DEFAULT_PARAM, name=name))
    thr, buf = p['entry_thr'], p['exit_buf']
    budget, cap = p['budget'] * RISK_MULT, p['cap'] * RISK_MULT

    ma_series = close.rolling(MA).mean()
    ma = float(ma_series.iloc[-1]); last = float(close.iloc[-1])
    ma50 = float(close.iloc[-MA_FAST:].mean())
    dist = (last / ma - 1) * 100; dist50 = (last / ma50 - 1) * 100
    below = (close < ma_series * buf).values
    days_below = 0
    for val in reversed(below):
        if val: days_below += 1
        else: break
    panic = vix_last is not None and vix_last > PANIC_VIX

    exit_price = round(ma * buf, 2)
    entry_cap = round(ma50 * thr, 2)
    stop_risk = round((last - exit_price) / last * 100, 1)
    # ★ 台股 yfinance 絕對價位可能失真→同時輸出『距現價 %』(scale-invariant,永遠正確,給券商換算用)
    entry_cap_pct = round((entry_cap / last - 1) * 100, 1)   # 進場上限 距現價
    exit_pct = round((exit_price / last - 1) * 100, 1)        # 停損線 距現價(負值)

    rs_score = None
    if len(close) > 130:
        r126 = float(close.iloc[-1] / close.iloc[-127] - 1)
        vol126 = float(close.pct_change().iloc[-126:].std() * (252 ** 0.5))
        rs_score = round(r126 / vol126, 3) if vol126 > 0 else None

    stop_dist_frac = max(stop_risk / 100.0, STOP_DIST_FLOOR)
    expo_raw = round(min(budget / stop_dist_frac, cap), 2)

    if last >= ma:
        state = 'risk_on'
        hold_action = f'續抱;建議曝險 {expo_raw*100:.0f}%(RiskTarget,不停利騎到跌破線)'
        suggested_expo = expo_raw
    elif last < exit_price:
        if panic and days_below < PANIC_DELAY:
            state = 'panic_watch'
            hold_action = f'恐慌觀察:VIX {vix_last:.0f}>{PANIC_VIX:.0f}、跌破{days_below}日,給{PANIC_DELAY}日確認別砍V底'
            suggested_expo = None
        else:
            reason = f'(恐慌已連{days_below}日確認)' if panic else ''
            state, hold_action = 'risk_off', f'出場/停損→轉現金{reason};曝險 0%'
            suggested_expo = 0.0
    else:
        state = 'warning'
        hold_action = f'逼近停損線,收盤跌破即出;貼線停損距僅 {stop_risk:.0f}%→建議曝險 {expo_raw*100:.0f}%'
        suggested_expo = expo_raw

    if last < ma:
        entry_state = 'no_entry'; entry_action = f'不進(在200MA NT${ma:.2f} 之下,等站回)'
    elif last >= entry_cap:
        entry_state = 'extended'
        entry_action = f'追高,等拉回到 NT${entry_cap:.2f} 以下(距50MA +{dist50:.0f}% > 門檻 +{(thr-1)*100:.0f}%)'
    else:
        entry_state = 'can_enter'
        extra = '；VIX高=恐慌綠燈,果斷進' if panic else ''
        entry_action = (f'可進場(距50MA +{dist50:.0f}% ≤ 門檻 +{(thr-1)*100:.0f}%)'
                        f'；停損距 -{stop_risk:.0f}% → 建議曝險 {expo_raw*100:.0f}%{extra}')

    return {'name': name, 'ma200': round(ma, 2), 'ma50': round(ma50, 2), 'close': round(last, 2),
            'dist_pct': round(dist, 2), 'dist50_pct': round(dist50, 2),
            'entry_thr': thr, 'exit_buf': buf, 'budget': round(budget, 4), 'cap': round(cap, 2),
            'exit_price': exit_price, 'entry_cap': entry_cap, 'stop_risk_pct': stop_risk,
            'entry_cap_pct': entry_cap_pct, 'exit_pct': exit_pct,
            'suggested_expo': suggested_expo, 'rs_score': rs_score, 'is_strongest': False,
            'days_below': days_below, 'panic': panic,
            'state': state, 'action': hold_action, 'hold_action': hold_action,
            'entry_state': entry_state, 'entry_action': entry_action}


def canary_signal(credit_closes: dict) -> dict:
    """台股信用哨:HYG+LQD+SMH 各看是否站上自己200MA → 健康比例=平滑縮倉乘數。
    全球信用(HYG/LQD)+費半領先(SMH);一壞×0.67、兩壞×0.33、三壞×0。"""
    assets = {}; healthy = 0
    for tk, close in credit_closes.items():
        ma = float(close.iloc[-MA:].mean()); last = float(close.iloc[-1])
        ok = last >= ma; healthy += 1 if ok else 0
        assets[tk] = {'name': CANARY_NAME.get(tk, tk), 'close': round(last, 2), 'ma200': round(ma, 2),
                      'dist_pct': round((last / ma - 1) * 100, 2), 'ok': ok}
    n = len(credit_closes) or 1
    frac = healthy / n
    bad = [CANARY_NAME.get(tk, tk) for tk, a in assets.items() if not a['ok']]
    if frac >= 1.0:
        note = '全球風險健康(HYG+LQD+SMH 皆站上200MA) → 曝險×100%'
    elif frac > 0:
        note = f'部分示警({"/".join(bad)} 跌破200MA) → 曝險×{frac:.0%}(平滑減碼)'
    else:
        note = '全示警(HYG+LQD+SMH 皆跌破200MA) → 曝險×0(全出)'
    return {'health': round(frac, 3), 'n_healthy': healthy, 'n_total': n, 'assets': assets,
            'credit_ok': frac >= 1.0,
            'state': 'credit_on' if frac >= 1.0 else ('credit_half' if frac > 0 else 'credit_off'),
            'note': note}


def compute_rotation_hold(out, today_strongest, prev, today_str):
    from datetime import date
    def parse(s):
        try: return date.fromisoformat(s)
        except Exception: return date.fromisoformat(today_str)
    def eligible(tk):
        d = out.get(tk)
        return bool(d) and d.get('state') == 'risk_on' and not d.get('credit_cut')
    today = parse(today_str)
    prev_tk = prev.get('ticker') if prev else None
    if prev_tk and eligible(prev_tk):
        held, since = prev_tk, prev.get('since', today_str)
        days = (today - parse(since)).days
        if days >= MIN_HOLD_DAYS and today_strongest and today_strongest != held and eligible(today_strongest):
            held, since, days = today_strongest, today_str, 0
    else:
        held = today_strongest if (today_strongest and eligible(today_strongest)) else None
        since, days = today_str, 0
    locked = bool(held) and days < MIN_HOLD_DAYS
    return {'ticker': held, 'since': since, 'days_held': days, 'locked': locked,
            'lock_days_left': max(0, MIN_HOLD_DAYS - days) if held else 0,
            'today_strongest': today_strongest, 'min_hold_days': MIN_HOLD_DAYS,
            'forced_out_prev': bool(prev_tk) and not eligible(prev_tk)}


def main():
    watch_have = []
    dl_list = CORE_TICKERS + list(TW_WATCH) + CANARY_TICKERS + [PANIC_TICKER]
    print(f"📥 下載 台股核心 + 候選 + 全球信用哨(HYG/LQD/SMH) + 恐慌計 ({len(dl_list)}檔, 3y)...")
    raw = yf.download(' '.join(dl_list), period='3y', interval='1d',
                      progress=False, group_by='ticker')

    def series(tk):
        try:
            s = raw[tk]['Close']; s = (s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s).dropna()
            return clean(s)
        except Exception:
            return None

    vix_last = None
    vs = series(PANIC_TICKER)
    if vs is not None and len(vs) > 0: vix_last = float(vs.iloc[-1])

    out = {}
    for tk in CORE_TICKERS:
        s = series(tk)
        if s is not None and len(s) >= MA:
            out[tk] = core_signal(s, vix_last, tk, TW_PARAMS[tk]['name'])
    # live候選:有≥MA資料才顯示(標註watch,不納入輪動)
    for tk, nm in TW_WATCH.items():
        s = series(tk)
        if s is not None and len(s) >= MA:
            d = core_signal(s, vix_last, tk, nm); d['watch_only'] = True
            out[tk] = d; watch_have.append(tk)

    canary = None
    credit_closes = {tk: series(tk) for tk in CANARY_TICKERS}
    credit_closes = {tk: s for tk, s in credit_closes.items() if s is not None and len(s) >= MA}
    if credit_closes:
        canary = canary_signal(credit_closes)

    # 橫斷面:可回測核心中(排除watch)挑RS最強
    ron = {tk: d for tk, d in out.items()
           if d['state'] == 'risk_on' and d.get('rs_score') is not None and not d.get('watch_only')}
    strongest = max(ron, key=lambda tk: ron[tk]['rs_score']) if ron else None
    if strongest: out[strongest]['is_strongest'] = True

    # 信用哨平滑乘數接曝險
    ch = canary['health'] if canary else 1.0
    if ch < 1.0:
        for tk, d in out.items():
            if d.get('suggested_expo') is not None:
                d['suggested_expo'] = round(d['suggested_expo'] * ch, 2)
            d['credit_mult'] = ch
            if ch <= 0:
                d['credit_cut'] = True
                d['hold_action'] = '🔴 全球風險全示警(HYG+LQD+SMH皆跌破200MA)→曝險砍0,等站回再進'
                d['action'] = d['hold_action']
                d['entry_state'] = 'no_entry'
                d['entry_action'] = '全示警期間不進場(等HYG/LQD/SMH站回200MA)'
            else:
                d['hold_action'] += f'  🟠部分示警→減碼半倉(仍可買·量×{ch:.0%})'
                d['action'] = d['hold_action']

    # 路線B 最低持有30日鎖(讀上次json)
    path = os.path.join(DASHBOARD_DIR, 'taiwan_status.json')
    prev_hold = None
    try:
        with open(path, encoding='utf-8') as f:
            prev_hold = json.load(f).get('rotation_hold')
    except Exception:
        pass
    today_str = datetime.now().strftime('%Y-%m-%d')
    rotation_hold = compute_rotation_hold(out, strongest, prev_hold, today_str)
    if rotation_hold.get('ticker') in out:
        out[rotation_hold['ticker']]['is_held'] = True

    data = {
        'market': 'TW', 'currency': 'TWD',
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'ma_window': MA, 'panic_vix': PANIC_VIX, 'vix': round(vix_last, 2) if vix_last is not None else None,
        'risk_mult': RISK_MULT, 'strongest': strongest, 'rotation_hold': rotation_hold,
        'anchor_a': '0052.TW',   # Route A 建議主力(回測Sharpe最高)
        'route_note': ('台股實證(research_taiwan_core/optimize.py):趨勢ETF擇時加分、高股息(0056)擇時傷已排除。'
                       '★台股無資本利得稅(僅證交稅0.1%)→Route B輪動淨報酬反勝A(+35.1 vs +32.1%),'
                       '不像美股偏A!建議台股偏 B(月輪動)或 A+B各半。Route A抱0052富邦科技為保守主力。'
                       '擇時=崩盤保險(2008擇時0% vs買持-48%),牛市讓利。'),
        'wealth_note': ('★台股財富槓桿(research_taiwan_optimize.py):用00631L元大台灣50正2(內建2x免融資)當衛星——'
                        'A40/B40+正2擇時衛星20% = Sharpe2.21/CAGR+35.7%/MDD-11.4%(同回撤多賺)。'
                        '正2須擇時(站上自己200MA才持有,崩盤會爆),且每日重設有盤整波動耗損,只在強趨勢用。'),
        'canary_note': ('★台股最佳信用哨=全球信用+費半SOXX(HYG+LQD+SOXX),非本地USDTWD(無效,Sharpe僅1.03)。'
                        '台股=全球半導體供應鏈,費半(SOXX)領先;三合健康比例×曝險(一壞×67%/兩壞×33%/三壞×0)。'),
        'data_note': ('⚠️ yfinance台股adjusted有假分割/尖刺(0050.TW 2014、0052.TW 2025)已splice清理;'
                      '報酬/Sharpe/回撤可信,絕對價位 yfinance 可能標錯(僅參考,以券商實際報價為準)。'),
        'rotation_manual_note': f'路線B最低持有{MIN_HOLD_DAYS}日鎖(換進至少抱一個月才准換、跌破200MA仍立刻出)。',
        'watch_note': ('主動式ETF(00991A/00981A等)歷史<1.5年無法回測,列為觀察候選;'
                       '滿200日MA200可算後才顯示狀態,但暫不納入輪動(無OOS驗證)。'),
        'watch_have': watch_have,
        'cores': out, 'canary': canary,
    }
    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    emoji = {'risk_on': '🟢', 'risk_off': '🔴', 'warning': '🟡', 'panic_watch': '🟠'}
    entry_emoji = {'can_enter': '🟢', 'extended': '🟡', 'no_entry': '🔴'}
    vix_str = f"{vix_last:.1f}" if vix_last is not None else "N/A"
    print(f"\n  恐慌計 VIX = {vix_str} ({'🔴恐慌' if vix_last and vix_last > PANIC_VIX else '🟢平靜'})")
    print(f"\n  台股核心擇時狀態(每ETF自校RiskTarget + 全球信用費半哨 + 恐慌容忍):")
    for tk, d in out.items():
        wt = ' [候選/不可回測]' if d.get('watch_only') else ''
        crown = ' 👑最強' if d.get('is_strongest') else (' 🅱️持有' if d.get('is_held') else '')
        ex = f"{d['suggested_expo']*100:.0f}%" if d['suggested_expo'] is not None else "維持"
        print(f"   {emoji[d['state']]} {tk:<10}{d['name']:<10}{crown}{wt} NT${d['close']:.2f} "
              f"(200MA {d['dist_pct']:+.1f}% / 50MA {d['dist50_pct']:+.1f}%) RS{d['rs_score'] if d['rs_score'] is not None else '—'}")
        print(f"          🏠 持有→ {d['action']}")
        print(f"          💵 空手→ {entry_emoji[d['entry_state']]} {d['entry_action']}")
        print(f"          🛑 停損線 NT${d['exit_price']:.2f}(停損距 -{d['stop_risk_pct']:.0f}%) · 進場上限 NT${d['entry_cap']:.2f} · 曝險 {ex}")
    if canary:
        ce = '🟢' if canary['health'] >= 1 else ('🟠' if canary['health'] > 0 else '🔴')
        astr = ' · '.join(f"{a['name']}{'🟢' if a['ok'] else '🔴'}{a['dist_pct']:+.1f}%" for a in canary['assets'].values())
        print(f"\n  信用+費半哨: {ce} 健康 {canary['n_healthy']}/{canary['n_total']} → 曝險×{canary['health']:.0%}  [{astr}]")
    rh = rotation_hold
    if rh.get('ticker'):
        lk = (f"🔒 鎖定中(抱{rh['days_held']}日,還{rh['lock_days_left']}日)" if rh['locked']
              else f"🔓 可換(今日最強={rh['today_strongest']})")
        print(f"\n  🅱️ 路線B 實際持有(最低{MIN_HOLD_DAYS}日鎖):{rh['ticker']}({out[rh['ticker']]['name']}, {rh['since']}進場) {lk}")
    else:
        print("\n  🅱️ 路線B 實際持有:空手(無核心站上200MA或信用砍0)")
    print(f"\n  Route A 建議主力:{data['anchor_a']} 富邦科技(回測Sharpe最高)")
    print(f"💾 {path}")


if __name__ == '__main__':
    main()
