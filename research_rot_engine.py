"""research_rot_engine.py - 多檔候選持有/挑選驗證用引擎(依 research_cache/rot_refutation.md §2/§3)
==========================================================================================
不修改、也不 import research_engine_2026q3 的回測函式;只沿用同一份資料(research_data_2026q3.load)。

修正清單(對照 rot_refutation.md §2):
  E1  兩種合格定義: 'above'(close ≥ 200MA,無遲滯 = live risk_on)與 'intrend'(遲滯,≥MA 進、<MA×buf 出)。
  E2  超額 Sharpe = mean(r−rf)/std(r−rf)·√252。美股 rf = ^IRX;台股 rf = 0% 或 1%(現金也賺同一個 rf)。
  E3  漂移模擬:以「槽位(slot)」記帳,每個槽位有 資產部位 A 與 現金部位 C(金額),每日隨價格/現金利率漂移,
      只在(a)槽位換標的/進出場、(b)再平衡日(槽位價值拉回 1/m)、(c)RT 曝險偏離超過區帶 時交易,依實際金額收成本。
  E4  RT 無交易區帶:|目標曝險 − 現有曝險| < band(預設 0.10)就不動。binary 永遠 1 或 0,不受影響。
  E5  換倉偏移:monthly 類用 reb_days(idx, offset)(offset=k → 月底後第 k 個交易日;k=0 即月底);
      lock 類用 phase=k(第 k 個交易日才開始運作,之前持現金)。
  E6  統一 buf:build_panel(..., buf_override=0.99)。
  E8  panel:各檔先在自己的交易日上算指標,再取共同交易日;報酬 = 共同交易日上的價格重算(被丟日報酬複利併入下一共同日)。
  E11 同波動:scale_to_vol() 用 §3.4.2 公式(含融資 1%/年、現金 rf)。
  E12 2× 成本:run_plan(..., cost_mult=2)。
  E13 年化固定 252(不修,註明)。
信用、vol-timing、panic 一律不套。
訊號用 t 日收盤,吃 t+1 日報酬。
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass, field

import research_data_2026q3 as D

# ---------------------------------------------------------------- 參數(與 live / 舊引擎一致)
US_PARAMS = {  # core_status.PARAMS 的 exit_buf / budget
    'SMH':  {'buf': 0.98, 'budget': 0.213},
    'SOXX': {'buf': 0.99, 'budget': 0.201},
    'QQQ':  {'buf': 1.00, 'budget': 0.199},
    'XLK':  {'buf': 0.98, 'budget': 0.213},
    'SPY':  {'buf': 1.00, 'budget': 0.190},
}
TW_PARAMS = {  # taiwan_status.TW_PARAMS 的 exit_buf / budget
    '0050.TW':   {'buf': 0.98, 'budget': 0.0863},
    '0052.TW':   {'buf': 0.99, 'budget': 0.0936},
    '006208.TW': {'buf': 0.99, 'budget': 0.0807},
    '0051.TW':   {'buf': 0.99, 'budget': 0.0787},
    '00757.TW':  {'buf': 1.00, 'budget': 0.1276},
}
DEFAULT = {'buf': 0.99, 'budget': 0.20}
CAP = 1.5
STOP_FLOOR = 0.02
FIN = 0.01 / 252          # 融資加碼(年 1%)

POOLS = {
    'US5':   {'tickers': ['SMH', 'SOXX', 'QQQ', 'XLK', 'SPY'], 'market': 'US', 'params': 'US'},
    'BROAD': {'tickers': list(D.US_BROAD), 'market': 'US', 'params': 'DEFAULT'},
    'TW4':   {'tickers': ['0050.TW', '0052.TW', '006208.TW', '0051.TW'], 'market': 'TW', 'params': 'TW'},
    'TW3L':  {'tickers': ['0050.TW', '0052.TW', '0051.TW'], 'market': 'TW', 'params': 'TW'},
    'TW5':   {'tickers': ['0050.TW', '0052.TW', '006208.TW', '0051.TW', '00757.TW'], 'market': 'TW', 'params': 'TW'},
}
WIDEST = {'US5': 'SPY', 'BROAD': 'SPY', 'TW4': '0050.TW', 'TW3L': '0050.TW', 'TW5': '0050.TW'}
# 事前規則分群(公開說明書同一標的指數/同一產業指數族;§1 A3)
GROUPS = {
    'US5': [['SMH', 'SOXX'], ['QQQ'], ['XLK'], ['SPY']],
    'TW4': [['0050.TW', '006208.TW'], ['0052.TW'], ['0051.TW']],
    'TW3L': [['0050.TW'], ['0052.TW'], ['0051.TW']],
    'TW5': [['0050.TW', '006208.TW'], ['0052.TW'], ['0051.TW'], ['00757.TW']],
    'BROAD': None,
}
SEGMENTS = {
    'US5':   [('S1', None, '2009-12-31'), ('S2', '2010-01-01', '2016-12-31'), ('S3', '2017-01-01', None)],
    'BROAD': [('S1', None, '2009-12-31'), ('S2', '2010-01-01', '2016-12-31'), ('S3', '2017-01-01', None)],
    'TW4':   [('S1', None, '2016-12-31'), ('S2', '2017-01-01', '2021-12-31'), ('S3', '2022-01-01', None)],
    'TW3L':  [('S0', None, '2012-12-31'), ('S1', '2013-01-01', '2016-12-31'),
              ('S2', '2017-01-01', '2021-12-31'), ('S3', '2022-01-01', None)],
    'TW5':   [],
}


def ticker_params(tk, mode):
    if mode == 'US':
        return US_PARAMS.get(tk, DEFAULT)
    if mode == 'TW':
        return TW_PARAMS.get(tk, DEFAULT)
    return DEFAULT


_DATA = None


def data():
    global _DATA
    if _DATA is None:
        _DATA = D.load()
    return _DATA


# ---------------------------------------------------------------- panel
@dataclass
class Panel:
    pool: str
    tickers: list
    market: str
    idx: pd.DatetimeIndex
    close: np.ndarray      # n×k 共同交易日收盤(台股已 clean)
    ret: np.ndarray        # n×k 共同交易日上重算的報酬(E8),第 0 列 = 0
    ma: np.ndarray
    above: np.ndarray      # close ≥ 200MA(live risk_on,無遲滯)
    intrend: np.ndarray    # 遲滯狀態(≥MA 進、<MA×buf 出;各檔在自己的交易日上算)
    rs: np.ndarray         # 126 日報酬 / 126 日年化波動(core_status rs_score)
    expo_rt: np.ndarray    # RiskTarget 目標曝險 clip(budget/max(停損距,2%),0,1.5)(未乘在場與否)
    cash: np.ndarray       # n 日現金報酬(= rf)
    rf: np.ndarray         # n 日無風險利率(超額 Sharpe 用)
    cost: float            # 單邊成本
    bufs: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)   # 第二段可放自訂分數矩陣等

    @property
    def n(self):
        return len(self.idx)

    @property
    def k(self):
        return len(self.tickers)

    def col(self, tk):
        return self.tickers.index(tk)


def _own_indicators(s: pd.Series, buf: float, budget: float) -> pd.DataFrame:
    c = s.values.astype(float)
    ma = s.rolling(200).mean().values
    n = len(c)
    it = np.zeros(n, dtype=bool)
    st = False
    for t in range(n):
        if np.isnan(ma[t]):
            st = False
        elif c[t] >= ma[t]:
            st = True
        elif c[t] < ma[t] * buf:
            st = False
        it[t] = st
    stopd = (c - ma * buf) / c
    ex = np.clip(budget / np.maximum(stopd, STOP_FLOOR), 0, CAP)
    r126 = s / s.shift(126) - 1
    v126 = s.pct_change().rolling(126).std() * np.sqrt(252)
    rs = (r126 / v126).values
    # 額外分數(給第二段 B2 用,只用 t 日以前資料)
    r60 = (s / s.shift(60) - 1).values
    r120 = (s / s.shift(120) - 1).values
    r252 = (s / s.shift(252) - 1).values
    mom_12_1 = (s.shift(21) / s.shift(252) - 1).values
    return pd.DataFrame({'c': c, 'ma': ma, 'intrend': it, 'expo': ex, 'rs': rs,
                         'ret_126': r126.values, 'rs_csm': (r60 + r120 + r252) / 3,
                         'mom_12_1': mom_12_1}, index=s.index)


def build_panel(pool: str, buf_override: float | None = None, tw_rf: float = 0.0,
                cost: float | None = None, tickers: list | None = None, start=None, end=None) -> Panel:
    """pool ∈ POOLS。tickers 可覆寫(例:N1 去重池),參數沿用該池的參數模式。
    tw_rf:台股年化 rf(0.0 或 0.01),現金也賺同一利率。美股 rf = ^IRX。"""
    cfg = POOLS[pool]
    tks = list(tickers or cfg['tickers'])
    tw = cfg['market'] == 'TW'
    d = data()
    own, bufs = {}, {}
    for tk in tks:
        s = d[tk]['Close'].dropna().astype(float)
        if tw:
            s = D.tw_clean(s)
        p = ticker_params(tk, cfg['params'])
        b = buf_override if buf_override is not None else p['buf']
        bufs[tk] = b
        own[tk] = _own_indicators(s, b, p['budget'])
    idx = None
    for tk in tks:
        f = own[tk]
        ok = f.index[~np.isnan(f['rs'].values) & ~np.isnan(f['ma'].values)]
        idx = ok if idx is None else idx.intersection(ok)
    if start:
        idx = idx[idx >= pd.Timestamp(start)]
    if end:
        idx = idx[idx <= pd.Timestamp(end)]
    R = {c: np.column_stack([own[tk][c].reindex(idx).values for tk in tks])
         for c in ['c', 'ma', 'intrend', 'expo', 'rs', 'ret_126', 'rs_csm', 'mom_12_1']}
    close = R['c'].astype(float)
    ret = np.zeros_like(close)
    ret[1:] = close[1:] / close[:-1] - 1          # E8:共同交易日上重算
    if tw:
        rfd = np.full(len(idx), tw_rf / 252.0)
    else:
        irx = d['^IRX']['Close'].reindex(idx).ffill().fillna(0).values / 100.0
        rfd = irx / 252.0
    c_ = cost if cost is not None else (0.001 if tw else 0.0005)
    return Panel(pool=pool, tickers=tks, market=cfg['market'], idx=idx, close=close, ret=ret,
                 ma=R['ma'].astype(float), above=(close >= R['ma']), intrend=R['intrend'].astype(bool),
                 rs=R['rs'].astype(float), expo_rt=R['expo'].astype(float), cash=rfd.copy(), rf=rfd.copy(),
                 cost=c_, bufs=bufs,
                 extra={'ret_126': R['ret_126'], 'rs_csm': R['rs_csm'], 'mom_12_1': R['mom_12_1']})


# ---------------------------------------------------------------- 換倉日
def reb_days(idx: pd.DatetimeIndex, offset: int = 0, freq: str = 'M') -> np.ndarray:
    """月底(freq='M')或季底('Q')往後推 offset 個交易日的布林陣列。offset=0 → 月底最後一個交易日。"""
    per = idx.to_period(freq)
    n = len(idx)
    ends = np.where(np.r_[per[1:] != per[:-1], True])[0]   # 每期最後一天(含最後一天資料)
    ends = ends[:-1]                                        # 最後一期未必結束,不算
    out = np.zeros(n, dtype=bool)
    tgt = ends + offset
    out[tgt[tgt < n]] = True
    return out


# ---------------------------------------------------------------- 計畫(plan)
@dataclass
class Plan:
    """S[t, s] = 第 s 個槽位在 t 日收盤後持有的資產欄位(-1 = 現金)。
    E[t, s] = 該槽位的目標曝險(binary=1;RT = expo_rt)。
    reb[t]  = t 日收盤把所有槽位的價值拉回 1/m(E3 的月再平衡)。
    events  = 換倉事件列表 (t, slot, old, new, kind) 供診斷。"""
    S: np.ndarray
    E: np.ndarray
    reb: np.ndarray
    events: list = field(default_factory=list)
    name: str = ''


def _exposure(P: Panel, S: np.ndarray, sizing: str) -> np.ndarray:
    E = np.zeros(S.shape)
    for s in range(S.shape[1]):
        a = S[:, s]
        on = a >= 0
        if sizing == 'binary':
            E[on, s] = 1.0
        else:
            E[on, s] = P.expo_rt[np.where(on)[0], a[on]]
    return E


def rotation_plan(P: Panel, elig: str | np.ndarray = 'above', score: str | np.ndarray = 'rs',
                  N: int = 1, lock: int = 21, switch: str = 'lock', reb: np.ndarray | None = None,
                  phase: int = 0, sizing: str = 'binary', refill: bool = True,
                  random_seed: int | None = None, rebalance_slots: bool | None = None,
                  delta: float | None = None, name: str = '') -> Plan:
    """通用「N 槽位輪動」狀態機(N=1 即單一持倉輪動)。
    elig : 'above'(BL-LIVE-F)/'intrend'(BL-LIVE-H)/ n×k 布林矩陣
    score: 'rs' / 'ret_126' / 'rs_csm' / 'mom_12_1' / n×k 矩陣(越大越強)
    switch: 'lock'  → 槽位持滿 lock 個交易日後,每天可換(live 規則)
            'reb'   → 只在 reb[t] 為 True 的日子可換(月換;reb 用 reb_days())
            'daily' → 每天可換(B3 用;可配 delta:新第一名分數須 > 現持有 + delta 才換)
    refill: 持有者失格時,當天由合格者中最強(未被其他槽位持有)補上(=live);False → 轉現金直到下個換倉機會(B4a)。
            空槽(現金)在換倉機會(lock 類 = 任何一天;reb 類 = reb 日)才補,除非 refill=True 時失格當天就補。
    random_seed: 不為 None → 結構相同的隨機版:每次「挑最強」改成在候選中均勻隨機挑;
            換倉機會時在全部合格者(含現持有)中均勻抽,抽到別檔才換。
    rebalance_slots: 是否在 reb 日把槽位價值拉回 1/N(預設:N>1 且有 reb 時 True)。
    phase: 第 phase 個交易日才開始運作(E5 lock 版起始相位)。"""
    n, k = P.n, P.k
    EL = (P.above if isinstance(elig, str) and elig == 'above' else
          P.intrend if isinstance(elig, str) and elig == 'intrend' else np.asarray(elig, bool))
    if isinstance(score, str):
        SC = P.rs if score == 'rs' else P.extra[score]
    else:
        SC = np.asarray(score, float)
    SC = np.where(np.isnan(SC), -np.inf, SC)
    rng = np.random.default_rng(random_seed) if random_seed is not None else None
    if reb is None:
        reb = reb_days(P.idx, 0)
    S = np.full((n, N), -1, dtype=int)
    since = np.full(N, -10 ** 9)
    cur = np.full(N, -1)
    events = []

    def pick(cands, t):
        if len(cands) == 0:
            return -1
        if rng is not None:
            return int(rng.choice(cands))
        return int(cands[np.argmax(SC[t, cands])])

    for t in range(n):
        if t < phase:
            continue
        el = np.where(EL[t])[0]
        opp = (switch == 'daily') or (switch == 'reb' and reb[t])
        # 1) 失格處理
        for s in range(N):
            a = cur[s]
            if a >= 0 and not EL[t, a]:
                held = set(cur[cur >= 0]) - {a}
                if refill:
                    new = pick(np.array([j for j in el if j not in held], dtype=int), t)
                else:
                    new = -1
                events.append((t, s, a, new, 'forced'))
                cur[s], since[s] = new, t
        # 2) 換倉機會(已持有的槽位)
        for s in range(N):
            a = cur[s]
            if a < 0:
                continue
            can = opp or (switch == 'lock' and (t - since[s]) >= lock)
            if not can:
                continue
            held_other = set(cur[cur >= 0]) - {a}
            cands = np.array([j for j in el if j not in held_other], dtype=int)
            if len(cands) == 0:
                continue
            if rng is not None:
                new = int(rng.choice(cands))
            else:
                if N == 1:
                    new = int(cands[np.argmax(SC[t, cands])])
                else:
                    # 只有當 a 不在合格者的前 N 名,才換成前 N 名中未持有的最強者
                    order = el[np.argsort(-SC[t, el], kind='stable')]
                    topN = set(order[:N].tolist())
                    if a in topN:
                        continue
                    pool_ = [j for j in order[:N] if j not in held_other]
                    if not pool_:
                        continue
                    new = int(pool_[0])
                if delta is not None and new != a and not (SC[t, new] > SC[t, a] + delta):
                    new = a
            if new != a:
                events.append((t, s, a, new, 'switch'))
                cur[s], since[s] = new, t
        # 3) 空槽補位:refill=True → 任何一天(=live:無持有 → 今日最強);
        #    refill=False → 只在換倉機會(reb 日 / lock 到期 / daily)
        for s in range(N):
            if cur[s] >= 0:
                continue
            if not (refill or opp or (switch == 'lock' and (t - since[s]) >= lock)):
                continue
            held = set(cur[cur >= 0])
            new = pick(np.array([j for j in el if j not in held], dtype=int), t)
            if new >= 0:
                events.append((t, s, -1, new, 'enter'))
                cur[s], since[s] = new, t
        S[t] = cur
    if rebalance_slots is None:
        rebalance_slots = N > 1
    rb = reb.copy() if rebalance_slots else np.zeros(n, dtype=bool)
    return Plan(S=S, E=_exposure(P, S, sizing), reb=rb, events=events, name=name)


def ew_plan(P: Panel, elig: str | np.ndarray = 'intrend', reb: np.ndarray | None = None,
            sizing: str = 'binary', name: str = 'BL-EW') -> Plan:
    """BL-EW(A1):k 個固定槽位(槽位 j = 標的 j),各自 intrend 擇時,不在場的槽位放現金;
    reb 日(預設月底)槽位價值拉回 1/k,月內漂移。"""
    EL = (P.intrend if isinstance(elig, str) and elig == 'intrend' else
          P.above if isinstance(elig, str) and elig == 'above' else np.asarray(elig, bool))
    S = np.where(EL, np.arange(P.k)[None, :], -1)
    if reb is None:
        reb = reb_days(P.idx, 0)
    return Plan(S=S, E=_exposure(P, S, sizing), reb=reb.copy(), events=[], name=name)


def single_timed_plan(P: Panel, tk: str, elig='intrend', sizing='binary') -> Plan:
    j = P.col(tk)
    EL = P.intrend if elig == 'intrend' else P.above
    S = np.where(EL[:, j], j, -1)[:, None]
    return Plan(S=S, E=_exposure(P, S, sizing), reb=np.zeros(P.n, bool), name=f'單押擇時 {tk}')


# ---------------------------------------------------------------- 模擬器(E3 漂移 + E4 區帶)
@dataclass
class Result:
    r: pd.Series            # 日報酬
    W: np.ndarray           # n×k 交易後資產權重(占 NAV)
    trades: int             # 資產層級的交易筆數(每個標的每一次加/減碼算一筆)
    turnover: float         # 累計換手(占 NAV)
    plan: Plan | None = None


def run_plan(P: Panel, plan: Plan, cost_mult: float = 1.0, band: float = 0.10,
             start: int = 0) -> Result:
    """槽位記帳模擬。t 日收盤後依 plan 調整,t+1 日吃報酬。
    band:RT 無交易區帶(binary 目標恆為 1,不受影響)。"""
    n, k = P.n, P.k
    S, E, RB = plan.S, plan.E, plan.reb
    m = S.shape[1]
    cost = P.cost * cost_mult
    A = np.zeros(m)          # 槽位資產部位金額
    C = np.full(m, 1.0 / m)  # 槽位現金部位金額
    asset = np.full(m, -1)
    nav_prev = 1.0
    out = np.zeros(n)
    Wh = np.zeros((n, k))
    trades, turn = 0, 0.0
    for t in range(n):
        # --- 1) 吃 t 日報酬(持倉為 t-1 收盤後的狀態)
        if t > 0:
            on = asset >= 0
            if on.any():
                A[on] *= 1.0 + P.ret[t, asset[on]]
            rc = np.where(C >= 0, P.cash[t], P.cash[t] + FIN)
            C *= 1.0 + rc
        nav = A.sum() + C.sum()
        if t >= start:
            # --- 2) t 日收盤調整
            pre = np.zeros(k)
            for s in range(m):
                if asset[s] >= 0:
                    pre[asset[s]] += A[s]
            V = A + C
            if RB[t]:
                V = np.full(m, V.sum() / m)
            newA = np.zeros(m)
            new_asset = S[t]
            for s in range(m):
                if new_asset[s] < 0:
                    continue
                tgt = E[t, s]
                if new_asset[s] != asset[s]:
                    newA[s] = tgt * V[s]
                else:
                    Vs0 = A[s] + C[s]
                    cur_e = A[s] / Vs0 if Vs0 > 0 else 0.0
                    e = tgt if abs(tgt - cur_e) >= band - 1e-12 else cur_e
                    newA[s] = e * V[s]
            post = np.zeros(k)
            for s in range(m):
                if new_asset[s] >= 0:
                    post[new_asset[s]] += newA[s]
            dA = np.abs(post - pre)
            tr = dA.sum()
            if tr > 1e-12 * nav:
                trades += int((dA > 1e-9 * nav).sum())
                turn += tr / nav
            tot_cost = tr * cost
            Vt = V.sum()
            C = V - newA - (tot_cost * V / Vt if Vt > 0 else 0.0)
            A, asset = newA, new_asset.copy()
            nav = A.sum() + C.sum()
            Wh[t] = post / nav if nav > 0 else 0.0
        out[t] = nav / nav_prev - 1
        nav_prev = nav
    return Result(r=pd.Series(out, index=P.idx), W=Wh, trades=trades, turnover=turn, plan=plan)


def buyhold(P: Panel, tk: str) -> pd.Series:
    return pd.Series(P.ret[:, P.col(tk)], index=P.idx)


# ---------------------------------------------------------------- 指標
def sub(r: pd.Series | np.ndarray, idx, a=None, b=None):
    s = r if isinstance(r, pd.Series) else pd.Series(r, index=idx)
    if a:
        s = s[s.index >= pd.Timestamp(a)]
    if b:
        s = s[s.index <= pd.Timestamp(b)]
    return s


def sharpe_ex(r, rf):
    x = np.asarray(r) - np.asarray(rf)
    sd = x.std(ddof=1)
    return x.mean() / sd * np.sqrt(252) if sd > 0 else np.nan


def cagr(r):
    r = np.asarray(r)
    return np.prod(1 + r) ** (252 / len(r)) - 1


def mdd(r):
    eq = np.cumprod(1 + np.asarray(r))
    return (eq / np.maximum.accumulate(eq) - 1).min()


def scale_to_vol(r, rf, sigma_T):
    """§3.4.2:r_k = rf + k(r−rf) − max(k−1,0)·0.01/252,k = σ_T/σ_H(全期)。"""
    r = np.asarray(r); rf = np.asarray(rf)
    sH = r.std(ddof=1) * np.sqrt(252)
    kk = sigma_T / sH
    return rf + kk * (r - rf) - max(kk - 1, 0) * FIN, kk


def metrics(r: pd.Series, rf: np.ndarray, sigma_T: float | None = None, res: Result | None = None) -> dict:
    r_ = np.asarray(r)
    o = {'CAGR': cagr(r_) * 100, 'Vol': r_.std(ddof=1) * np.sqrt(252) * 100,
         'ShEx': sharpe_ex(r_, rf), 'MDD': mdd(r_) * 100}
    o['Calmar'] = o['CAGR'] / abs(o['MDD']) if o['MDD'] < 0 else np.nan
    if sigma_T is not None:
        rk, kk = scale_to_vol(r_, rf, sigma_T)
        o['k'] = kk
        o['svCAGR'] = cagr(rk) * 100
        o['svMDD'] = mdd(rk) * 100
        o['svCalmar'] = o['svCAGR'] / abs(o['svMDD']) if o['svMDD'] < 0 else np.nan
    if res is not None:
        yrs = len(r_) / 252
        o['Expo'] = res.W.sum(1).mean()
        o['Nhold'] = (res.W > 1e-9).sum(1).mean()
        o['Trades/yr'] = res.trades / yrs
    return o


# ---------------------------------------------------------------- stationary block bootstrap(Politis-Romano)
def sb_indices(n: int, B: int = 2000, mean_block: float = 63, seed: int = 0) -> np.ndarray:
    """回傳 B×n 的重抽索引(平均區塊長 mean_block,環狀)。"""
    rng = np.random.default_rng(seed)
    p = 1.0 / mean_block
    new_block = rng.random((B, n)) < p
    new_block[:, 0] = True
    starts = rng.integers(0, n, size=(B, n))
    idx = np.empty((B, n), dtype=np.int64)
    # 向量化:區塊起點取 starts,其他位置 = 前一位置 + 1
    # 對每列:block_id = cumsum(new_block);位置 = start_of_block + (t − t_block_start)
    t = np.arange(n)
    bid = np.cumsum(new_block, axis=1) - 1
    for b in range(B):
        nb = np.where(new_block[b])[0]
        st = starts[b, nb]
        off = t - nb[bid[b]]
        idx[b] = (st[bid[b]] + off) % n
    return idx


def boot_delta_sharpe(rA, rB, rf, IDX: np.ndarray, chunk: int = 250) -> dict:
    """配對 ΔSharpe_excess(A − B)。p = 單尾 P*(Δ* ≤ 0)(百分位法);90% CI = 5%/95% 分位。"""
    xa = np.asarray(rA) - np.asarray(rf)
    xb = np.asarray(rB) - np.asarray(rf)
    obs = sharpe_ex(rA, rf) - sharpe_ex(rB, rf)
    ds = []
    for i in range(0, IDX.shape[0], chunk):
        I = IDX[i:i + chunk]
        a = xa[I]; b = xb[I]
        sa = a.mean(1) / a.std(1, ddof=1)
        sb = b.mean(1) / b.std(1, ddof=1)
        ds.append((sa - sb) * np.sqrt(252))
    ds = np.concatenate(ds)
    return {'d': obs, 'p': float((ds <= 0).mean()), 'lo': float(np.quantile(ds, 0.05)),
            'hi': float(np.quantile(ds, 0.95)), 'B': len(ds)}


def holm(pvals: dict) -> dict:
    items = sorted(pvals.items(), key=lambda x: x[1])
    m = len(items)
    out, run = {}, 0.0
    for i, (k_, p) in enumerate(items):
        adj = min(1.0, (m - i) * p)
        run = max(run, adj)
        out[k_] = run
    return out
