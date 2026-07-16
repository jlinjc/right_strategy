"""
research_sr_lines.py — 支撐壓力線還有沒有可提升的用法?(Jason 2026-07-17)
========================================================================
已判定:F3貼支撐買=雜訊(符號隨指數翻)、F4頭上壓力=已按指數重校準(QQQ/XLK/SPY無壓制力、
SOXX≤1%、SMH+0%)。這裡測四個「還沒測過」的 S/R 用法(全部 PIT:pivot 到 i+k 才確認):

  B 多次觸碰壓力:同一價位帶被觸碰≥2次的壓力,是不是比單次觸碰更硬的牆?
    (若是→resist_warn 可升級成「距離×觸碰數」的更聰明準則)
  C 突破回測(壓力翻支撐):近15日內剛突破前高、現價回踩到該前高±1.5%=教科書買點。
    事後報酬 vs 全部 risk_on 日,真的比較好嗎?
  D 結構停損下注:RiskTarget 的停損距改用「最近已確認前波低點」(比 MA200 線更貼價格結構)。
    只動 sizing、進出場不變;budget 重校準對齊平均曝險(資本配置線鐵律,不然是偷加槓桿)。
  E 支撐追蹤停損:跌破「最近已確認前波低點」就出場(結構破壞),疊在 MA200 出場之上。
    ⚠️舊研究已否決 MA20/50/100 追蹤止損(全輸 MA200);swing-low 版沒測過,誠實補測,預期難贏。

誠實尺 shift(1),2006 起含 GFC。標的 SMH/QQQ/SPY。
"""
import sys, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd, yfinance as yf
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import core_status as C

TICKERS = ['SMH', 'QQQ', 'SPY']
START = '2006-01-01'
K = 5                 # fractal pivot 確認根數
LOOKBACK = 120        # 壓力回看
TOUCH_BAND = 0.0075   # 觸碰帶 ±0.75%
RETEST_WIN = 15       # 突破後幾日內回踩算 retest
RETEST_BAND = 0.015   # 回踩帶 ±1.5%


def dl(s):
    x = yf.download(s, start=START, auto_adjust=True, progress=False)
    if isinstance(x.columns, pd.MultiIndex):
        x.columns = x.columns.get_level_values(0)
    return x[['High', 'Low', 'Close']].dropna()


def perf(r):
    r = r.dropna(); sd = r.std(); eq = (1 + r).cumprod()
    return {'sharpe': r.mean() / sd * np.sqrt(252) if sd > 0 else np.nan,
            'cagr': (eq.iloc[-1] ** (252 / len(r)) - 1) * 100, 'mdd': (eq / eq.cummax() - 1).min() * 100}


def pivots(high, low, k):
    """已確認 pivot:回傳 {confirm_idx: [(kind,'H'/'L', level)]}(PIT)。"""
    n = len(high); out = {}
    for i in range(k, n - k):
        if high[i] == high[i - k:i + k + 1].max():
            out.setdefault(i + k, []).append(('H', float(high[i])))
        if low[i] == low[i - k:i + k + 1].min():
            out.setdefault(i + k, []).append(('L', float(low[i])))
    return out


def analyze(tk):
    df = dl(tk)
    high, low, close = df['High'].values, df['Low'].values, df['Close'].values
    n = len(close); idx = df.index
    ma200 = pd.Series(close, index=idx).rolling(200).mean().values
    ret = pd.Series(close, index=idx).pct_change()
    piv = pivots(high, low, K)
    p = C.PARAMS.get(tk, C.DEFAULT_PARAM)
    buf, budget, cap = p['exit_buf'], p['budget'], p['cap']

    # 逐日維護:active 壓力(120日內已確認 swing high)、最近已確認 swing low
    fwd21 = np.array([close[i + 21] / close[i] - 1 if i + 21 < n else np.nan for i in range(n)])
    fwd63 = np.array([close[i + 63] / close[i] - 1 if i + 63 < n else np.nan for i in range(n)])
    risk_on = (~np.isnan(ma200)) & (close >= ma200)

    active_h = []                      # (confirm_idx, level)
    last_sl = np.full(n, np.nan)       # 最近已確認 swing low
    res_lvl = np.full(n, np.nan)       # 最近上方壓力
    res_touch = np.zeros(n)            # 該壓力帶被觸碰次數(以已確認pivot計)
    retest = np.zeros(n, dtype=bool)   # 突破回測日
    broken = []                        # (level, expiry_idx) 已被突破的前高
    cur_sl = np.nan
    for t in range(n):
        for kind, lv in piv.get(t, []):
            if kind == 'H':
                active_h.append((t, lv))
            else:
                cur_sl = lv
        active_h = [(ci, lv) for ci, lv in active_h if ci >= t - LOOKBACK]
        last_sl[t] = cur_sl
        above = [lv for _, lv in active_h if lv > close[t]]
        if above:
            r = min(above)
            res_lvl[t] = r
            res_touch[t] = sum(1 for _, lv in active_h if abs(lv / r - 1) <= TOUCH_BAND)
        # 突破偵測:昨日壓力今日被收盤突破 → 記錄 retest 窗
        if t > 0 and not np.isnan(res_lvl[t - 1]) and close[t] > res_lvl[t - 1]:
            broken.append((res_lvl[t - 1], t + RETEST_WIN))
        broken = [(lv, ex) for lv, ex in broken if ex >= t]
        if risk_on[t] and any(abs(close[t] / lv - 1) <= RETEST_BAND for lv, ex in broken):
            retest[t] = True

    print("=" * 96)
    print(f"  {tk}")
    print("=" * 96)

    # ── B 多次觸碰壓力 ──
    m = risk_on & (~np.isnan(res_lvl)) & ((res_lvl / close - 1) <= 0.03)
    one = m & (res_touch <= 1); multi = m & (res_touch >= 2)
    print(f"  [B] 壓力觸碰次數(距離≤3%的壓力日):多次觸碰=更硬的牆?")
    for name, mask in [('單次觸碰', one), ('≥2次觸碰', multi)]:
        f1, f3 = fwd21[mask], fwd63[mask]
        f1, f3 = f1[~np.isnan(f1)], f3[~np.isnan(f3)]
        if len(f1):
            print(f"     {name:8} n={len(f1):4d}  21日 {f1.mean()*100:+5.1f}%/勝{(f1>0).mean()*100:3.0f}%"
                  f"   63日 {f3.mean()*100:+5.1f}%/勝{(f3>0).mean()*100:3.0f}%")

    # ── C 突破回測 ──
    base = risk_on.copy()
    print(f"  [C] 突破回測(近{RETEST_WIN}日剛破前高、回踩±{RETEST_BAND*100:.1f}%)vs 全部risk_on日:")
    for name, mask in [('回測日', retest), ('全部risk_on', base)]:
        f1, f3 = fwd21[mask], fwd63[mask]
        f1, f3 = f1[~np.isnan(f1)], f3[~np.isnan(f3)]
        print(f"     {name:10} n={len(f1):4d}  21日 {f1.mean()*100:+5.1f}%/勝{(f1>0).mean()*100:3.0f}%"
              f"   63日 {f3.mean()*100:+5.1f}%/勝{(f3>0).mean()*100:3.0f}%")

    # ── D/E 狀態機(共用進場;D只換sizing、E加結構出場)──
    exit_line = ma200 * buf
    def run(mode):
        expo = np.zeros(n); in_pos = False; last_base = 0.0; exits = 0
        for t in range(n):
            if np.isnan(ma200[t]):
                continue
            reclaim = close[t] >= ma200[t]
            if not in_pos:
                if reclaim:
                    in_pos = True
            else:
                stop = exit_line[t]
                if mode == 'E' and not np.isnan(last_sl[t]):
                    stop = max(stop, last_sl[t] * 0.99)     # 結構停損(跌破前波低點-1%)
                if close[t] < stop:
                    in_pos = False; exits += 1
            if in_pos and reclaim:
                if mode == 'D' and not np.isnan(last_sl[t]) and last_sl[t] * 0.99 > exit_line[t]:
                    sd = (close[t] - last_sl[t] * 0.99) / close[t]   # 結構停損距(較緊)
                else:
                    sd = (close[t] - exit_line[t]) / close[t]
                last_base = min(budget / max(sd, C.STOP_DIST_FLOOR), cap)
                expo[t] = last_base
            elif in_pos:
                expo[t] = last_base
        return pd.Series(expo, index=idx), exits

    eb, xb = run('BASE')
    ed, xd = run('D')
    ee, xe = run('E')
    # D 的 budget 重校準:平均曝險對齊 BASE(否則結構停損較緊→曝險灌大=偷加槓桿)
    scale = eb.mean() / ed.mean() if ed.mean() > 0 else 1.0
    ed_n = (ed * scale).clip(upper=cap)
    print(f"  [D/E] 狀態機(進場同、誠實尺):")
    print(f"     {'變體':22}{'Sharpe':>9}{'CAGR':>9}{'MDD':>9}{'出場':>6}{'平均曝險':>9}")
    for name, ex, xs in [('BASE(MA200線,現用)', eb, xb),
                         (f'D 結構停損下注(×{scale:.2f}對齊)', ed_n, xd),
                         ('E 支撐追蹤停損出場', ee, xe)]:
        r = (ex.shift(1) * ret).dropna()
        st = perf(r)
        print(f"     {name:22}{st['sharpe']:>9.3f}{st['cagr']:>8.1f}%{st['mdd']:>8.1f}%{xs:>6d}{ex.mean()*100:>8.0f}%")
    print()


def main():
    for tk in TICKERS:
        analyze(tk)


if __name__ == '__main__':
    main()
