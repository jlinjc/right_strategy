"""research_engine_2026q3.py - 2026-09 三題稽核用的「獨立重寫」回測引擎
========================================================================
刻意不 import 舊 research_*.py 的回測函式:照 live 檔(core_status.py / taiwan_status.py)的規則
從頭寫一次,讓結論不繼承舊研究可能的 bug。

規則對齊 live:
  趨勢狀態(遲滯):收盤 ≥ 200MA → 在場;收盤 < 200MA×exit_buf → 出場;中間 = 維持前一狀態。
  RiskTarget:曝險 = clip(budget / max(停損距,2%), 0, cap),停損距 = (close − 200MA×buf)/close
  RS 分數:126 日報酬 ÷ 126 日年化波動(core_status.py rs_score)
  訊號用 t 日收盤,吃 t+1 日報酬(無 look-ahead)。
成本:換手每 1 單位曝險收 cost(美股 0.05%,台股 0.10%);曝險>1 的部分付融資 = 現金利率+1%/年。
現金:^IRX(13 週國庫券)年化 → 日報酬;缺值補 0。
"""
import numpy as np
import pandas as pd

US_PARAMS = {
    'SMH':  {'buf': 0.98, 'thr': 1.10, 'budget': 0.213},
    'SOXX': {'buf': 0.99, 'thr': 1.07, 'budget': 0.201},
    'QQQ':  {'buf': 1.00, 'thr': 1.07, 'budget': 0.199},
    'XLK':  {'buf': 0.98, 'thr': 1.10, 'budget': 0.213},
    'SPY':  {'buf': 1.00, 'thr': 1.05, 'budget': 0.190},
}
TW_PARAMS = {
    '0050.TW':   {'buf': 0.98, 'thr': 1.06, 'budget': 0.0863},
    '0052.TW':   {'buf': 0.99, 'thr': 1.08, 'budget': 0.0936},
    '006208.TW': {'buf': 0.99, 'thr': 1.08, 'budget': 0.0807},
    '0051.TW':   {'buf': 0.99, 'thr': 1.08, 'budget': 0.0787},
    '00757.TW':  {'buf': 1.00, 'thr': 1.12, 'budget': 0.1276},
}
DEFAULT = {'buf': 0.99, 'thr': 1.08, 'budget': 0.20}


def params(tk):
    return US_PARAMS.get(tk) or TW_PARAMS.get(tk) or DEFAULT


def cash_daily(data, index):
    irx = data['^IRX']['Close'].reindex(index).ffill().fillna(0) / 100.0
    return (irx / 252.0).values


def indicators(close: pd.Series, p: dict):
    c = close.values.astype(float)
    ma = close.rolling(200).mean().values
    ma50 = close.rolling(50).mean().values
    n = len(c)
    intrend = np.zeros(n, dtype=bool)
    st = False
    for t in range(n):
        if np.isnan(ma[t]):
            st = False
        elif c[t] >= ma[t]:
            st = True
        elif c[t] < ma[t] * p['buf']:
            st = False
        intrend[t] = st
    stopd = (c - ma * p['buf']) / c
    expo_rt = np.clip(p['budget'] / np.maximum(stopd, 0.02), 0, 1.5)
    expo_rt = np.where(intrend, expo_rt, 0.0)
    r126 = close / close.shift(126) - 1
    vol126 = close.pct_change().rolling(126).std() * np.sqrt(252)
    rs = (r126 / vol126).values
    extended = c >= ma50 * p['thr']
    return {'c': c, 'ma': ma, 'ma50': ma50, 'intrend': intrend, 'stopd': stopd,
            'expo_rt': expo_rt, 'rs': rs, 'extended': extended}


def panel(data, tickers, start=None, end=None, tw=False):
    """對齊所有標的到共同交易日(各自先算指標再對齊,避免 MA 被對齊缺值汙染)。"""
    ind, rets = {}, {}
    for tk in tickers:
        s = data[tk]['Close'].dropna()
        if tw:
            import taiwan_status as T
            s = T.clean(s)
        I = indicators(s, params(tk))
        df = pd.DataFrame({k: v for k, v in I.items()}, index=s.index)
        df['ret'] = s.pct_change().values
        ind[tk] = df
    idx = None
    for tk in tickers:
        ok = ind[tk].index[~np.isnan(ind[tk]['rs'].values) & ~np.isnan(ind[tk]['ma'].values)]
        idx = ok if idx is None else idx.intersection(ok)
    if start: idx = idx[idx >= pd.Timestamp(start)]
    if end: idx = idx[idx <= pd.Timestamp(end)]
    out = {tk: ind[tk].reindex(idx) for tk in tickers}
    return idx, out


def run_weights(idx, P, W: np.ndarray, tickers, cash, cost=0.0005):
    """W[t, j] = t 日收盤決定的曝險 → 吃 t+1 報酬。回傳日報酬序列。"""
    R = np.nan_to_num(np.column_stack([P[tk]['ret'].values for tk in tickers]))
    Wp = np.vstack([np.zeros((1, W.shape[1])), W[:-1]])          # t 日吃 t-1 收盤決定的權重
    g = Wp.sum(1)
    turn = np.abs(np.diff(np.vstack([np.zeros((1, W.shape[1])), Wp]), axis=0)).sum(1)
    port = (Wp * R).sum(1) + (1 - np.minimum(g, 1)) * cash - np.maximum(g - 1, 0) * (cash + 0.01 / 252) - turn * cost
    port[0] = 0.0
    return pd.Series(port, index=idx)


def rotation_weights(idx, P, tickers, mode='lock', lock_days=21, sizing='binary', seed=None, score='rs'):
    """單一持倉輪動。mode: 'daily'(每天換最強) / 'lock'(live:持滿 lock_days 交易日才准換) /
       'monthly' / 'quarterly'(只在月/季末換)/ 'random'(合格者中隨機挑,同 lock 規則;RS 選股的對照組)。
       持有標的失格(出趨勢)→ 立刻換到當下最強合格者(同 live compute_rotation_hold)。"""
    rng = np.random.default_rng(seed)
    n, k = len(idx), len(tickers)
    IT = np.column_stack([P[tk]['intrend'].values for tk in tickers])
    RS = np.column_stack([P[tk][score].values for tk in tickers])
    EX = np.column_stack([P[tk]['expo_rt'].values for tk in tickers])
    W = np.zeros((n, k))
    held, since = None, 0
    switches = 0
    months = idx.to_period('M'); quarters = idx.to_period('Q')
    for t in range(n):
        elig = np.where(IT[t])[0]
        if len(elig) == 0:
            best = None
        elif mode == 'random':
            best = None  # 隨機只在需要換時抽
        else:
            best = elig[np.argmax(RS[t, elig])]
        can_switch = False
        if held is not None and IT[t, held]:
            if mode == 'daily':
                can_switch = True
            elif mode in ('lock', 'random'):
                can_switch = (t - since) >= lock_days
            elif mode == 'monthly':
                can_switch = t + 1 < n and months[t + 1] != months[t]
            elif mode == 'quarterly':
                can_switch = t + 1 < n and quarters[t + 1] != quarters[t]
            if can_switch:
                if mode == 'random':
                    new = rng.choice(elig) if len(elig) else None
                else:
                    new = best
                if new is not None and new != held:
                    held, since = new, t; switches += 1
        else:
            if len(elig):
                new = rng.choice(elig) if mode == 'random' else best
                if new != held:
                    switches += 1 if held is not None else 0
                held, since = new, t
            else:
                held = None
        if held is not None:
            W[t, held] = 1.0 if sizing == 'binary' else EX[t, held]
    return W, switches


def timed_single_weights(P, tickers, sizing='binary'):
    return np.column_stack([(P[tk]['intrend'].values.astype(float) if sizing == 'binary'
                             else P[tk]['expo_rt'].values) for tk in tickers])


def metrics(r: pd.Series, label=''):
    r = r.dropna()
    if len(r) < 20:
        return {}
    eq = (1 + r).cumprod()
    yrs = len(r) / 252
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    vol = r.std() * np.sqrt(252)
    sh = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan
    mdd = (eq / eq.cummax() - 1).min()
    return {'label': label, 'CAGR%': round(cagr * 100, 1), 'Vol%': round(vol * 100, 1),
            'Sharpe': round(sh, 2), 'MDD%': round(mdd * 100, 1),
            'Calmar': round(cagr / abs(mdd), 2) if mdd < 0 else np.nan}


def table(rows):
    df = pd.DataFrame([x for x in rows if x])
    return df.to_string(index=False)
