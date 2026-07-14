"""
research_price_action.py - 純裸價格結構 + 量 的前瞻報酬研究
=========================================================================
Jason 要求:回歸最純的技術分析——純價格變動 / 支撐 / 壓力 / 成交量,
找出比 MA200/MA50 更多的「端倪與方向」。

刻意「不用指標」(ADX/RSI/MACD 已被 research_technical 證偽=價格重排),
只用裸結構:
  F1 相對成交量 relvol = vol / SMA20(vol)        —— 量本身(非價格衍生)
  F2 量縮拉回  (回檔 + 量縮)                      —— 經典「健康拉回」說法
  F3 距最近前波低點(支撐)距離%                   —— 真實水平支撐(fractal pivot,非均線)
  F4 上方前波高點(壓力)剩餘空間%                 —— 頭上壓力壓不壓報酬
  F5 量價確認突破 (創60日新高 × 突破當日量能)     —— 帶量突破 vs 假突破
  F6 下跌量能高潮 (大跌 + 爆量 capitulation)       —— 恐慌拋售後的反彈

方法(沿用 research_entry_timing):前瞻報酬分桶,非狀態機。
  對每個交易日按特徵分桶,量該日買進後未來 63/126 日裸報酬 + 勝率。
  ⚠️ 長天期 forward return 高度重疊 → 桶間相對比較有效,絕對顯著性高估。
  支撐/壓力用『已確認』fractal pivot(往前看 k 根確認)→ 無 look-ahead。
"""
import sys, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd, yfinance as yf
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

TICKERS = ['SMH', 'QQQ', 'SPY']
HZ = [63, 126]          # 3m / 6m
PIVOT_K = 5             # fractal:左右各 k 根 → 局部低/高點,i+k 才確認(PIT)


def load(tk):
    df = yf.download(tk, start='2006-01-01', auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()


def swing_levels(df, k=PIVOT_K):
    """回傳兩個 Series:每天『已確認』的最近前波低點價 / 前波高點價(PIT)。
       swing low 在 i:low[i]=min(low[i-k..i+k]);到 i+k 才知道 → 從 i+k 起可用。"""
    low, high = df['Low'].values, df['High'].values
    n = len(df)
    # 逐天維護「到今天為止已確認的最後 swing low/high」(pivot 到 i+k 才確認 → 無 look-ahead)
    confirmed_low = np.full(n, np.nan)
    confirmed_high = np.full(n, np.nan)
    for i in range(k, n - k):
        if low[i] == low[i - k:i + k + 1].min():
            confirmed_low[i + k] = low[i]     # i+k 當天確認此 swing low
        if high[i] == high[i - k:i + k + 1].max():
            confirmed_high[i + k] = high[i]
    cl = pd.Series(confirmed_low, index=df.index).ffill()
    ch = pd.Series(confirmed_high, index=df.index).ffill()
    return cl, ch


def bucket_report(feat, fwd, mask, labels, edges=None, name=''):
    """feat: Series 特徵; fwd: dict h->Series 前瞻報酬; mask: 只看這些日子。
       edges=None → 用五分位;否則用給定 edges。印每桶未來報酬+勝率。"""
    f = feat[mask].dropna()
    if len(f) < 100:
        print(f"  {name}: 樣本太少({len(f)}),略過"); return
    if edges is None:
        q = pd.qcut(f.rank(method='first'), 5, labels=False)
    else:
        q = pd.cut(f, edges, labels=False)
    print(f"  {name}  (n={len(f)})")
    header = "     bucket        " + "   ".join(f"{h}d報酬  勝率" for h in HZ)
    print(header)
    for b in sorted(pd.Series(q).dropna().unique()):
        idx = f.index[q == b]
        cells = []
        for h in HZ:
            r = fwd[h].reindex(idx).dropna()
            cells.append(f"{r.mean()*100:+6.1f}%  {(r>0).mean()*100:4.0f}%")
        lab = labels[int(b)] if labels and int(b) < len(labels) else f"Q{int(b)}"
        print(f"     {lab:14}" + "   ".join(cells) + f"   (n={len(idx)})")


def event_report(cond, fwd, mask, name=''):
    """二元事件:cond=True 的日子 vs mask 全體 的前瞻報酬對照。"""
    ev = cond & mask
    base = mask
    print(f"  {name}")
    for tag, m in [('事件', ev), ('基準(全體)', base)]:
        cells = []
        for h in HZ:
            r = fwd[h].reindex(fwd[h].index[m]).dropna()
            cells.append(f"{r.mean()*100:+6.1f}%  勝{(r>0).mean()*100:3.0f}%")
        print(f"     {tag:10} " + "   ".join(cells) + f"   (n={int(m.sum())})")


def analyze(tk):
    df = load(tk)
    close, vol = df['Close'], df['Volume']
    ret = close.pct_change()
    ma200 = close.rolling(200).mean()
    above = close > ma200                          # 系統真正會考慮進場的 regime
    fwd = {h: (close.shift(-h) / close - 1) for h in HZ}

    relvol = vol / vol.rolling(20).mean()
    cl, ch = swing_levels(df)
    supp_dist = (close - cl) / close * 100          # 距下方支撐 %(越小=越貼支撐)
    resi_room = (ch - close) / close * 100          # 上方壓力剩餘空間 %(<=0 表示已突破)

    print("=" * 78)
    print(f"  {tk}  裸價格結構研究  |  {df.index[0].date()}→{df.index[-1].date()}  "
          f"|  站上200MA天數 {int(above.sum())}/{len(df)}")
    print("=" * 78)

    # 無條件 benchmark(站上200MA隨便買)
    print("\n[benchmark] 站上200MA 無條件買進的前瞻報酬:")
    cells = []
    for h in HZ:
        r = fwd[h].reindex(fwd[h].index[above]).dropna()
        cells.append(f"{h}d {r.mean()*100:+.1f}% 勝{(r>0).mean()*100:.0f}%")
    print("     " + "   ".join(cells))

    print("\n[F1] 相對成交量 relvol(進場當日量/20日均量)—— 只看站上200MA:")
    bucket_report(relvol, fwd, above, None, name='relvol 五分位(Q0低量→Q4爆量)')

    print("\n[F2] 量縮拉回(近5日下跌 & 量縮<0.8):")
    pull = (close < close.shift(5)) & (relvol < 0.8)
    event_report(pull, fwd, above, name='量縮拉回 vs 基準')

    print("\n[F3] 距下方支撐距離%(fractal 前波低點,越小越貼支撐)—— 站上200MA:")
    bucket_report(supp_dist, fwd, above & (supp_dist > 0), None,
                  name='支撐距離 五分位(Q0最貼支撐→Q4最遠)')

    print("\n[F4] 上方壓力剩餘空間%(fractal 前波高點)—— 站上200MA & 尚未突破:")
    bucket_report(resi_room, fwd, above & (resi_room > 0), None,
                  name='壓力空間 五分位(Q0壓力就在頭上→Q4海闊天空)')

    print("\n[F5] 帶量突破 vs 假突破(今日創60日新高):")
    newhigh = close >= close.rolling(60).max()
    strong = newhigh & (relvol > 1.5)
    weak = newhigh & (relvol < 1.0)
    event_report(strong, fwd, pd.Series(True, index=df.index), name='帶量突破(創新高×量>1.5)')
    event_report(weak, fwd, pd.Series(True, index=df.index), name='弱量突破(創新高×量<1.0)')

    print("\n[F6] 下跌量能高潮(單日跌>3% & 量>2倍)—— 全樣本(抄底型):")
    capit = (ret < -0.03) & (relvol > 2.0)
    event_report(capit, fwd, pd.Series(True, index=df.index), name='爆量大跌 vs 全體')


if __name__ == '__main__':
    only = sys.argv[1] if len(sys.argv) > 1 else None
    for tk in ([only] if only else TICKERS):
        analyze(tk)
        print()
