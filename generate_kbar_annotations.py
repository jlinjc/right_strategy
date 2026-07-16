"""
generate_kbar_annotations.py — 每日 K 棒 PIT 標註產生器(視覺化用)
========================================================================
對 SPY/QQQ/SMH/SOXX/XLK/006208/0050 的每一根日K,算出「在那一天(只用當天及以前的
資料,嚴禁未來函數)系統會把這根標成什麼」:可買 / 可小買 / 偏貴少買 / 追高別追 /
鋸齒觀望 / 頭上壓力等突破 / 爆量別追 / 逼近停損 / 恐慌觀察別砍V底 / 跌破出場 / 信用清倉。

★ PIT 保證:每根 K 棒的標註只用 close[:t+1]。所有特徵都是 trailing:
  MA200/MA50/rolling vol/相對量/60日新高 皆回看;上方壓力用『已確認』fractal swing high
  (pivot 到 i+k 才確認,只納入 confirm≤t 的);恐慌/信用哨用當天為止的 MA200。零 shift(-h)。
  → 歷史每一天看到的標註,就是那天收盤後系統真正會給的建議,回頭驗證可信度用。

參數與 live 完全同源:US 用 core_status.PARAMS + 結構常數;台股用 taiwan_status.TW_PARAMS + clean()。
信用哨:US=HYG+LQD;台股=HYG+LQD+SOXX(全球信用+費半)。健康比例×budget(=live 平滑減碼)。

輸出 Web_Dashboard/kbar_annotations.json,前端「每日K棒標註」頁讀取。
"""
import os, sys, json, warnings
from collections import deque
from datetime import datetime
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd, yfinance as yf

from scanner_base import DASHBOARD_DIR
import core_status as C
import taiwan_status as T

START_DL = '2006-01-01'      # 下載起點(含2008 GFC → 比較統計才公正,不高估自適應;顯示K棒仍從 START_OUT)
START_OUT = '2016-01-01'     # 輸出起點(早於此的暖身段不顯示)

US = [('SPY', 'S&P500'), ('QQQ', '那斯達克100'), ('SMH', '半導體SMH'),
      ('SOXX', '費半SOXX'), ('XLK', '科技XLK')]
TW = [('006208.TW', '富邦台50'), ('0050.TW', '元大台灣50')]
US_CANARY = ['HYG', 'LQD']
TW_CANARY = ['HYG', 'LQD', 'SOXX']
BULL_CAP = 2.0          # 自適應版:牛市(MA200上彎+信用健康)cap 1.5→2.0;轉熊自動收回(research_regime_adaptive.py)
SWEEP_CAPS = [1.5, 1.75, 2.0, 2.5, 3.0]   # 積極度取捨掃描(1.5=原版;讓 Jason 挑「睡得著的回撤」那檔)


def _clean_ohlc(df: pd.DataFrame, is_tw: bool) -> pd.DataFrame:
    """回傳含 Open/High/Low/Close/Volume 的乾淨 df。台股用 taiwan_status.clean 清 close,
       再把同一調整因子套到 O/H/L(維持 K 棒一致),Volume 留原始。"""
    df = df.dropna(subset=['Close']).copy()
    if not is_tw or len(df) < 3:
        return df
    clean_close = T.clean(df['Close'])
    factor = (clean_close / df['Close']).reindex(df.index).fillna(1.0)
    for col in ['Open', 'High', 'Low']:
        if col in df:
            df[col] = df[col] * factor
    df['Close'] = clean_close.reindex(df.index)
    return df.dropna(subset=['Close'])


def _resistances(high: np.ndarray, close: np.ndarray, k: int, lookback: int):
    """每日『已確認』的最近上方壓力(最低的、仍高於當日收盤的前波高點)。PIT:pivot 到 i+k 才納入。"""
    n = len(high)
    res = [None] * n
    piv_by_confirm = {}
    for i in range(k, n - k):
        if high[i] == high[i - k:i + k + 1].max():
            piv_by_confirm.setdefault(i + k, []).append((i, float(high[i])))
    active = deque()
    for t in range(n):
        for pv in piv_by_confirm.get(t, []):
            active.append(pv)
        while active and active[0][0] < t - lookback:
            active.popleft()
        c = close[t]
        above = [pr for (_, pr) in active if pr > c]
        res[t] = min(above) if above else None
    return res


def _canary_health(dates: pd.DatetimeIndex, canary_closes: dict) -> np.ndarray:
    """每日信用哨健康比例(各資產 close≥自己MA200 的比例),PIT trailing,對齊 dates 後 ffill。"""
    oks = []
    for s in canary_closes.values():
        ma = s.rolling(C.MA).mean()
        ok = (s >= ma).astype(float)
        ok = ok.where(ma.notna())                       # MA 未暖身→NaN
        oks.append(ok.reindex(dates).ffill())
    if not oks:
        return np.ones(len(dates))
    H = pd.concat(oks, axis=1).mean(axis=1)             # 健康比例
    return H.fillna(1.0).values                          # 暖身前視為健康(不誤砍)


def _kcat(label: str) -> str:
    if '信用示警' in label: return 'credit_clear'
    if '跌破線' in label:   return 'exit'
    if '恐慌觀察' in label: return 'panic_watch'
    if '逼近停損' in label: return 'near_stop'
    if '爆量突破' in label: return 'blowoff'
    if '頭上壓力' in label: return 'resistance'
    if '鋸齒' in label:     return 'chop'
    if '追高' in label:     return 'chase'
    if '偏貴' in label:     return 'expensive'
    if '可小買' in label:   return 'buy_small'
    if '可買' in label:     return 'buy_full'
    return 'other'


def _buy_word(expo, quiet):
    if expo >= 1.0:   w = f'可買(足量~{expo*100:.0f}%)'
    elif expo >= 0.5: w = f'可小買(~{expo*100:.0f}%)'
    else:             w = f'偏貴·買少量(~{(expo or 0)*100:.0f}%)'
    return w + ('·無量緩破健康' if quiet else '')


def _state_expo(c, ma, el, h, vx, bull, budget, cap, floor, bull_cap):
    """狀態機曝險(進出邏輯完全相同,只差牛市 cap=bull_cap;bull_cap=cap 即原版=公正比較核心)。"""
    n = len(c)
    below = c < el
    db = np.zeros(n, int); run = 0
    for t in range(n):
        run = run + 1 if below[t] else 0
        db[t] = run
    expo = np.zeros(n); in_pos = False; last_base = 0.0
    for t in range(n):
        credit_off = h[t] <= 0
        reclaim = (not np.isnan(ma[t])) and c[t] >= ma[t]
        panic = (not np.isnan(vx[t])) and vx[t] > C.PANIC_VIX
        cap_eff = ((bull_cap[t] if hasattr(bull_cap, '__len__') else bull_cap) if bull[t] else cap)
        if not in_pos:
            if reclaim and not credit_off:
                in_pos = True
        else:
            if credit_off:
                in_pos = False
            elif below[t] and not (panic and db[t] < C.PANIC_DELAY):
                in_pos = False
        if in_pos and reclaim:
            sd = (c[t] - el[t]) / c[t]
            last_base = min(budget / max(sd, floor), cap_eff)
            expo[t] = last_base * h[t]
        elif in_pos:
            expo[t] = last_base * h[t]
    return expo


def _stats(r):
    r = r[~np.isnan(r)]
    if len(r) < 20:
        return {}
    mu, sd = r.mean(), r.std()
    eq = np.cumprod(1 + r)
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return {'sharpe': round(float(mu / sd * np.sqrt(252)), 3) if sd > 0 else None,
            'cagr': round(float(eq[-1] ** (252 / len(r)) - 1) * 100, 1),
            'mdd': round(float(dd) * 100, 1)}


def annotate(ohlc: pd.DataFrame, params: dict, vix: pd.Series, canary_closes: dict):
    """對單一標的逐日產生 PIT 標註。回傳 bars list。"""
    df = ohlc
    dates = df.index
    close = df['Close'].values.astype(float)
    high = df['High'].values.astype(float) if 'High' in df else close
    low = df['Low'].values.astype(float) if 'Low' in df else close
    openp = df['Open'].values.astype(float) if 'Open' in df else close
    vol = df['Volume'].values.astype(float) if 'Volume' in df else np.zeros(len(df))

    s_close = pd.Series(close, index=dates)
    ma200 = s_close.rolling(C.MA).mean().values
    ma50 = s_close.rolling(C.MA_FAST).mean().values
    relvol = (pd.Series(vol, index=dates) /
              pd.Series(vol, index=dates).rolling(C.VOL_WIN).mean()).values
    prior60 = s_close.rolling(C.BREAKOUT_WIN).max().shift(1).values
    vix_al = vix.reindex(dates).ffill().values if vix is not None else np.full(len(dates), np.nan)
    health = _canary_health(dates, canary_closes)
    resist = _resistances(high, close, C.PIVOT_K, C.RESIST_LOOKBACK)
    slope20 = s_close.rolling(C.MA).mean().diff(20).values     # MA200 斜率(20日)
    _n = len(close)
    f63 = np.array([close[i + 63] / close[i] - 1 if i + 63 < _n else np.nan for i in range(_n)])  # 事後63日(診斷證據)

    thr, buf = params['entry_thr'], params['exit_buf']
    budget, cap = params['budget'], params['cap']

    # 連續跌破日數(trailing)
    exit_line = ma200 * buf
    below = close < exit_line
    days_below = np.zeros(len(close), dtype=int)
    run = 0
    for t in range(len(close)):
        run = run + 1 if below[t] else 0
        days_below[t] = run

    bars = []
    out_idx = []
    for t in range(len(close)):
        d = dates[t]
        if d < pd.Timestamp(START_OUT) or np.isnan(ma200[t]):
            continue
        c = close[t]; ma = ma200[t]; ep = exit_line[t]
        ec = ma50[t] * thr if not np.isnan(ma50[t]) else None
        sr = (c - ep) / c                                   # 停損距(fraction,可負)
        h = health[t]
        vx = vix_al[t]
        panic = (not np.isnan(vx)) and vx > C.PANIC_VIX

        # RiskTarget 曝險(信用健康×budget,同 live 平滑減碼)
        budget_eff = budget * h
        expo = None
        if sr > 0:
            expo = round(min(budget_eff / max(sr, C.STOP_DIST_FLOOR), cap), 2)

        note_bits = []
        # ── 判定標註 ──
        if h <= 0:
            tone, label, expo = 'red', '信用示警·清倉', 0.0
            note_bits.append('信用哨全示警(HYG/LQD 皆跌破200MA)')
        elif c < ep:
            if panic and days_below[t] < C.PANIC_DELAY:
                tone, label, expo = 'amber', f'恐慌觀察·別砍V底(跌破{days_below[t]}日)', None
                note_bits.append(f'VIX {vx:.0f}>30,歷史此情境21日中位+3.3%/67%上漲→給3日確認')
            else:
                tone, label, expo = 'red', '跌破線·出場/空手', 0.0
                note_bits.append(f'收盤 < 停損線 {ep:.2f}(200MA×{buf})')
        elif c < ma:
            tone, label = 'amber', '逼近停損·別加碼'
            note_bits.append(f'介於停損線與200MA之間,收盤跌破 {ep:.2f} 即出')
        else:
            # risk_on
            newhigh = (not np.isnan(prior60[t])) and c >= prior60[t]
            rv = relvol[t]
            blowoff = newhigh and (not np.isnan(rv)) and rv >= C.BLOWOFF_VOL
            quiet_bo = newhigh and (not np.isnan(rv)) and rv <= C.QUIET_VOL
            chop = abs(c / ma - 1) <= C.CHOP_ZONE_PCT / 100.0
            r = resist[t]
            room = (r / c - 1) if r else None
            if ec is not None and c >= ec:
                tone, label = 'amber', '追高·別追(等拉回50MA)'
                note_bits.append(f'距50MA +{(c/ma50[t]-1)*100:.0f}% > 門檻 +{(thr-1)*100:.0f}%')
            elif chop:
                tone, label = 'amber', '鋸齒區·觀望(信心低)'
                note_bits.append(f'離200MA 僅 {(c/ma-1)*100:+.0f}%(剛站上假訊號多)')
            elif blowoff:
                tone, label = 'amber', '爆量突破·別追那根'
                note_bits.append(f'創{C.BREAKOUT_WIN}日新高 × 量 {rv:.1f}倍=短線耗竭')
            elif room is not None and room <= 0.03:
                tone, label = 'amber', f'頭上壓力+{room*100:.0f}%·等突破'
                note_bits.append(f'前波高點 {r:.2f} 就在上方')
            else:
                if expo is not None and expo >= 1.0:
                    tone, label = 'green', f'可買(足量~{expo*100:.0f}%)'
                elif expo is not None and expo >= 0.5:
                    tone, label = 'green', f'可小買(~{expo*100:.0f}%)'
                else:
                    tone, label = 'lime', f'偏貴·買少量(~{(expo or 0)*100:.0f}%)'
                if quiet_bo:
                    label += '·無量緩破健康'
                note_bits.append(f'停損距 -{sr*100:.0f}%'
                                 + (f' · 上方壓力 {r:.2f}(+{room*100:.0f}%)' if r else ' · 藍天無壓'))
        if 0 < h < 1:
            note_bits.append(f'信用部分示警→曝險×{h:.0%}')

        # ── 診斷(PIT 偵測條件 + 事後63日證據):找標籤不合邏輯/更好操作處 ──
        cg = _kcat(label)
        vix_now = vix_al[t]; vix5 = vix_al[t - 5] if t >= 5 else np.nan
        vspk = np.nanmax(vix_al[max(0, t - 20):t + 1]) if t > 0 else vix_now
        vfall = (not np.isnan(vix_now)) and (not np.isnan(vix5)) and vix_now < vix5
        slope_up = (not np.isnan(slope20[t])) and slope20[t] > 0
        dg = None
        if cg == 'credit_clear' and c >= ma and (not np.isnan(vspk)) and vspk > C.PANIC_VIX and vfall:
            dg = ('D1', 'V底回補漏接', '🟢 勇敢買:指數已收復200MA、VIX從尖峰回落→別等信用慢哨,直接回補', 'high')
        elif cg == 'exit' and days_below[t] <= 2:
            dg = ('D2', '出場洗損風險', '🟠 第1天別砍:改「跌破觀察」,連續3日+信用同壞才出(常反彈)', 'high')
        elif cg == 'expensive':
            dg = ('D3', '偏貴誤標', '空手改「別追高(等拉回)」;曝險已極低=其實別新進,持有者續抱', 'med')
        elif cg == 'chop' and slope_up:
            dg = ('D4', '鋸齒誤判', 'MA200上彎=發射台,改「可買」別觀望', 'med')
        elif cg == 'buy_full' and expo is not None and expo >= cap * 0.95 and (not slope_up or h < 1):
            dg = ('D5', '做頭型滿槓桿', '拉回若為較低高點/信用轉弱→別上滿150%,減半', 'low')
        diag = None
        if dg:
            fwd = round(float(f63[t]) * 100, 1) if not np.isnan(f63[t]) else None
            diag = {'code': dg[0], 'name': dg[1], 'better': dg[2], 'sev': dg[3],
                    'original': label, 'fwd63': fwd}

        # ── 自適應版標籤(牛市積極/熊市保守):進出同原版,只差牛市 cap 拉高 → 曝險上限更高 ──
        bull = slope_up and h >= 1.0
        cap_eff = BULL_CAP if bull else cap
        a_expo, a_label, a_tone = expo, label, tone
        if tone in ('green', 'lime') and sr > 0:
            a_expo = round(min((budget * h) / max(sr, C.STOP_DIST_FLOOR), cap_eff), 2)
            if bull:                                     # 牛市買區→用更高上限重算下注量
                a_label = _buy_word(a_expo, '無量緩破' in label)
                a_tone = 'green' if a_expo >= 0.5 else 'lime'
        adaptive = {'regime': 'bull' if bull else 'bear',
                    'label': ('🐂積極 ' if bull else '🐻保守 ') + a_label,
                    'tone': a_tone, 'expo': a_expo}

        bars.append({
            'time': d.strftime('%Y-%m-%d'),
            'open': round(float(openp[t]), 2), 'high': round(float(high[t]), 2),
            'low': round(float(low[t]), 2), 'close': round(float(c), 2),
            'volume': float(vol[t]),
            'ma200': round(float(ma), 2), 'exit': round(float(ep), 2),
            'label': label, 'tone': tone,
            'expo': expo, 'note': ' · '.join(note_bits), 'diag': diag, 'adaptive': adaptive,
        })
        out_idx.append(t)

    # 同類診斷連續多日→只留每段第一天(避免markers爆量)
    prev = None
    for b in bars:
        code = b['diag']['code'] if b.get('diag') else None
        if code is not None and code == prev:
            b['diag'] = None
        prev = code

    # ── 兩版狀態機權益曲線 + 全期統計(公正比較:同進出,只差牛市 cap) ──
    bull_arr = (~np.isnan(slope20)) & (slope20 > 0) & (health >= 1.0)
    floor = C.STOP_DIST_FLOOR
    ret = np.zeros(_n); ret[1:] = close[1:] / close[:-1] - 1
    valid = ~np.isnan(ma200)
    eb = _state_expo(close, ma200, exit_line, health, vix_al, bull_arr, budget, cap, floor, cap)
    ea = _state_expo(close, ma200, exit_line, health, vix_al, bull_arr, budget, cap, floor, BULL_CAP)
    # vol-timing:牛市 cap 隨波動縮放(平靜加碼/動盪縮);EWMA 波動預測,QQQ+SMH 穩健(research_sizing_deepen.py)
    rv_ser = s_close.pct_change().ewm(span=C.VOL_WIN).std() * np.sqrt(252)
    rvs = rv_ser.values
    medvs = rv_ser.rolling(C.VOL_MED).median().values
    vcap = np.clip(cap * medvs / rvs, cap * C.VOL_CAP_LO_MULT, cap * C.VOL_CAP_HI_MULT)
    vcap = np.where(np.isnan(vcap), cap, vcap)
    ev = _state_expo(close, ma200, exit_line, health, vix_al, bull_arr, budget, cap, floor, vcap)
    rb = np.zeros(_n); rb[1:] = eb[:-1] * ret[1:]
    ra = np.zeros(_n); ra[1:] = ea[:-1] * ret[1:]
    rvret = np.zeros(_n); rvret[1:] = ev[:-1] * ret[1:]
    stats = {'base': _stats(rb[valid]), 'adapt': _stats(ra[valid]), 'vol': _stats(rvret[valid]),
             'bull_share': round(float(np.mean(bull_arr[valid])) * 100, 0),
             'avg_expo_base': round(float(np.mean(eb[valid])) * 100, 0),
             'avg_expo_adapt': round(float(np.mean(ea[valid])) * 100, 0),
             'avg_expo_vol': round(float(np.mean(ev[valid])) * 100, 0)}
    eqb = np.cumprod(1 + rb); eqa = np.cumprod(1 + ra); eqv = np.cumprod(1 + rvret)
    if out_idx:
        b0, a0, v0 = eqb[out_idx[0]] or 1.0, eqa[out_idx[0]] or 1.0, eqv[out_idx[0]] or 1.0
        eq_base = [round(float(eqb[t] / b0 * 100), 2) for t in out_idx]
        eq_adapt = [round(float(eqa[t] / a0 * 100), 2) for t in out_idx]
        eq_vol = [round(float(eqv[t] / v0 * 100), 2) for t in out_idx]
    else:
        eq_base = eq_adapt = eq_vol = []
    # ★積極度取捨掃描:牛市 cap 1.5→3.0 各自的 報酬↔回撤(讓 Jason 挑睡得著那檔)
    sweep = []
    for bc in SWEEP_CAPS:
        ex = _state_expo(close, ma200, exit_line, health, vix_al, bull_arr, budget, cap, floor, bc)
        rr = np.zeros(_n); rr[1:] = ex[:-1] * ret[1:]
        st = _stats(rr[valid])
        sweep.append({'cap': bc, 'sharpe': st.get('sharpe'), 'cagr': st.get('cagr'),
                      'mdd': st.get('mdd'), 'avg_expo': round(float(np.mean(ex[valid])) * 100, 0)})
    extra = {'stats': stats, 'eq_base': eq_base, 'eq_adapt': eq_adapt, 'eq_vol': eq_vol, 'sweep': sweep}
    return bars, extra


def main():
    all_syms = [s for s, _ in US + TW] + ['HYG', 'LQD', 'SOXX', '^VIX']
    all_syms = list(dict.fromkeys(all_syms))
    print(f"📥 下載 {len(all_syms)} 檔({START_DL}起): {' '.join(all_syms)} ...")
    raw = yf.download(' '.join(all_syms), start=START_DL, interval='1d',
                      progress=False, group_by='ticker', auto_adjust=True, threads=True)

    def get(sym, is_tw=False):
        try:
            d = raw[sym][['Open', 'High', 'Low', 'Close', 'Volume']]
            return _clean_ohlc(d, is_tw)
        except Exception as e:
            print(f"  ⚠️ {sym} 取得失敗: {e}")
            return None

    vix = None
    try:
        vix = raw['^VIX']['Close'].dropna()
    except Exception:
        pass
    canary_raw = {}
    for tk in ['HYG', 'LQD', 'SOXX']:
        d = get(tk)
        if d is not None:
            canary_raw[tk] = d['Close']

    tickers_out = []
    for sym, name in US + TW:
        is_tw = sym.endswith('.TW')
        d = get(sym, is_tw)
        if d is None or len(d) < C.MA + 30:
            continue
        params = (T.TW_PARAMS.get(sym) if is_tw else C.PARAMS.get(sym)) or C.DEFAULT_PARAM
        canary_closes = {k: canary_raw[k] for k in (TW_CANARY if is_tw else US_CANARY) if k in canary_raw}
        bars, extra = annotate(d, params, vix, canary_closes)
        if not bars:
            continue
        last = bars[-1]
        tickers_out.append({'symbol': sym, 'name': name,
                            'market': 'tw' if is_tw else 'us',
                            'params': {'entry_thr': params['entry_thr'], 'exit_buf': params['exit_buf'],
                                       'budget': params['budget'], 'cap': params['cap']},
                            'bars': bars, 'stats': extra['stats'], 'sweep': extra['sweep'],
                            'eq_base': extra['eq_base'], 'eq_adapt': extra['eq_adapt'],
                            'eq_vol': extra['eq_vol']})
        st = extra['stats']
        print(f"  ✓ {sym:11} {name:8} {len(bars)} 根K  最新[{last['time']}] {last['label']}"
              f"  | 原Sharpe {st['base'].get('sharpe')}/自適應 {st['adapt'].get('sharpe')}"
              f" CAGR {st['base'].get('cagr')}→{st['adapt'].get('cagr')}%")

    out = {
        'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'pit_note': '每根K棒的標註只用當天及以前資料(trailing MA/壓力用已確認pivot),無未來函數。',
        'tone_legend': {'green': '可買/可小買', 'lime': '偏貴少量', 'amber': '觀望/追高/壓力/逼近停損/恐慌觀察', 'red': '跌破出場/信用清倉'},
        'tickers': tickers_out,
    }
    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    path = os.path.join(DASHBOARD_DIR, 'kbar_annotations.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"💾 {path}  ({os.path.getsize(path)//1024} KB, {len(tickers_out)} 檔)")


if __name__ == '__main__':
    main()
