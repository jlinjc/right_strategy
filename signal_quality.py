"""
signal_quality.py - 訊號品質評分 + 信心分級下注 + 組合熱度（live 進場端）
=====================================================================================
首席指揮官的「賺錢三件事」裡的後兩件：下注大小、不要在相關標的同時爆倉。
進場濾網(#1財報 #2板塊)管 edge 純度；本模組管「這個 edge 值多少注、以及
現在還能不能再開一注」。全部 point-in-time，無 look-ahead。

提供：
  pullback_quality(df, idx)        拉回品質 0~100（量縮/波動收斂/非瀑布/守住20MA）
  extension_state(df, idx, rankpct) 趨勢延伸：對200MA乖離 + 是否末段
  conviction(...)                  綜合信心分數 → 分級(A/B/C) → 風險乘數
  portfolio_heat(positions, smap)  讀現有持倉算組合熱度 + 板塊計數（live 防爆倉）

哲學：信心分級下注是「bounded fractional-Kelly」——edge 高就下大，但設上下限、
錨定已驗證的 1% 基準，避免單一估計誤差造成破產。研究(2024-25)一致支持：
用 fractional Kelly、隨信心調整部位、設硬上限。
"""

import numpy as np
import pandas as pd

from backtest_strategies import calc_atr


# ============================================================
# 拉回品質：分辨「健康量縮拉回(會續漲)」與「出貨式瀑布(會破)」
# ============================================================
def pullback_quality(df: pd.DataFrame, idx: int) -> dict:
    """
    回傳 {score 0~100, vol_contraction_pct, range_contraction, waterfall, above_20ma}
    依據 (VCP / 低量拉回 文獻，2025)：
      - 量縮：拉回 5 日均量 vs 拉回前趨勢腿(idx-30~idx-10)均量，縮 ≥50% 最佳
      - 波動收斂：近 5 日 ATR < 近 20 日 ATR (coiling)
      - 非瀑布：近 5 日內無單日跌幅 > 2×ATR (有序拉回，非恐慌出貨)
      - 守結構：收盤仍 >= 20MA
    """
    out = {'score': 50.0, 'vol_contraction_pct': None,
           'range_contraction': None, 'waterfall': False, 'above_20ma': None}
    if idx < 35:
        return out

    close = df['Close']
    vol = df['Volume']
    atr_now = float(calc_atr(df['High'].iloc[idx-25:idx+1], df['Low'].iloc[idx-25:idx+1],
                             close.iloc[idx-25:idx+1], 5).iloc[-1] or 0)
    atr_base = float(calc_atr(df['High'].iloc[idx-25:idx+1], df['Low'].iloc[idx-25:idx+1],
                              close.iloc[idx-25:idx+1], 20).iloc[-1] or 0)

    # 量縮
    trend_vol = float(vol.iloc[idx-30:idx-10].mean())
    pull_vol = float(vol.iloc[idx-4:idx+1].mean())
    vol_contraction = (1 - pull_vol / trend_vol) * 100 if trend_vol > 0 else 0.0

    # 波動收斂
    range_contraction = (atr_now / atr_base) if atr_base > 0 else 1.0

    # 瀑布偵測：近 5 日任一日跌幅 > 2×ATR
    waterfall = False
    if atr_base > 0:
        for j in range(idx-4, idx+1):
            drop = float(close.iloc[j-1] - close.iloc[j])
            if drop > 2 * atr_base:
                waterfall = True
                break

    # 守 20MA
    ma20 = float(close.iloc[idx-19:idx+1].mean())
    above_20ma = float(close.iloc[idx]) >= ma20

    # 合成分數
    score = 50.0
    score += float(np.clip(vol_contraction, -30, 50)) * 0.6      # 量縮 +，放量 −
    score += (1.0 - float(np.clip(range_contraction, 0.5, 1.5))) * 30  # 收斂 +
    score += 8 if above_20ma else -12
    score -= 25 if waterfall else 0
    score = float(np.clip(score, 0, 100))

    out.update({
        'score': round(score, 0),
        'vol_contraction_pct': round(vol_contraction, 0),
        'range_contraction': round(range_contraction, 2),
        'waterfall': bool(waterfall),
        'above_20ma': bool(above_20ma),
    })
    return out


# ============================================================
# 趨勢延伸狀態：別把最大注押在噴出末段的拉回
# ============================================================
def extension_state(df: pd.DataFrame, idx: int, ext_rank_pct: float = None) -> dict:
    """
    ext_above_200ma_pct : 收盤對 200MA 的乖離(%)
    ext_rank            : 此乖離的「廣股池」橫斷面百分位 (僅顯示，不用於末段判定)
    ext_self_pct        : 此乖離在「該股自己過去 252 日」乖離分布的百分位
    late_stage          : ext_self_pct > 90 → 該股比自己過去一年都更伸展 = 真‧末段風險

    為何用自相對而非橫斷面：高 RS 領導股結構上就遠離 200MA，用全池橫斷面會把
    幾乎所有領導股都誤標末段(無鑑別力)。「比自己過去都更伸展」才抓得到噴出竭盡。
    """
    if idx < 200:
        return {'ext_above_200ma_pct': None, 'ext_rank': ext_rank_pct,
                'ext_self_pct': None, 'late_stage': False}
    close = df['Close']
    ma200_now = float(close.iloc[idx-199:idx+1].mean())
    ext_now = (float(close.iloc[idx]) / ma200_now - 1) * 100 if ma200_now > 0 else 0.0

    # 自相對：過去 252 日，每日「收盤/200MA−1」的分布
    lo = max(200, idx - 252)
    ma200_series = close.rolling(200).mean()
    hist_ext = (close.iloc[lo:idx+1] / ma200_series.iloc[lo:idx+1] - 1).dropna()
    ext_self_pct = None
    late = False
    if len(hist_ext) >= 60:
        ext_self_pct = float((hist_ext <= (ext_now / 100)).mean() * 100)
        late = ext_self_pct > 90
    return {'ext_above_200ma_pct': round(ext_now, 1),
            'ext_rank': round(ext_rank_pct, 0) if ext_rank_pct is not None else None,
            'ext_self_pct': round(ext_self_pct, 0) if ext_self_pct is not None else None,
            'late_stage': bool(late)}


# ============================================================
# 信心分級下注（bounded fractional-Kelly）
# ============================================================
def conviction(rs_pct, sector_rs, pead_active, pq_score, late_stage,
               risk_floor=0.006, risk_cap=0.014, base_risk=0.010) -> dict:
    """
    把多個獨立 edge 線索合成 0~100 信心分 → 分級 → 風險乘數(錨定 base 1%)。
    權重邏輯(非過擬合，可解釋)：
      RS 0.35 | 板塊RS 0.20 | 拉回品質 0.20 | PEAD 0.15 | 末段扣分 0.10
    """
    def norm(x, lo, hi):
        if x is None:
            return 0.5
        return float(np.clip((x - lo) / (hi - lo), 0, 1))

    s = (0.35 * norm(rs_pct, 80, 100)
         + 0.20 * norm(sector_rs, 30, 100)
         + 0.20 * norm(pq_score, 30, 90)
         + 0.15 * (1.0 if pead_active else 0.0)
         + 0.10 * (0.0 if late_stage else 1.0))
    score = round(s * 100, 0)

    if score >= 70:
        tier, mult = 'A', 1.35
    elif score >= 50:
        tier, mult = 'B', 1.0
    else:
        tier, mult = 'C', 0.7

    eff_risk = float(np.clip(base_risk * mult, risk_floor, risk_cap))
    return {'score': score, 'tier': tier, 'risk_mult': mult,
            'eff_risk_pct': round(eff_risk * 100, 2)}


# ============================================================
# 組合熱度 + 板塊計數（讀 live positions.json，防相關標的同時爆倉）
# ============================================================
def portfolio_heat(positions: list, sector_map: dict, account_size: float) -> dict:
    """
    positions: exit_manager 的 LivePosition dict 清單
               (需含 entry_price / current_stop|initial_stop / shares / ticker)
    回傳 {open_heat_pct, open_gross_pct, sector_counts{sector:n}, held_tickers[]}

    open_heat_pct  : 停損距離風險 Σ(entry-stop)×sh —— 假設「停損成交」的左尾度量
    open_gross_pct : 名目曝險 Σ entry×sh —— 假設「整組同向跳空」的真實左尾度量
                     (高相關 AI book 的真正風險是群聚跳空，heat 量不到，需 gross 補)
    """
    heat_dollars = 0.0
    gross_dollars = 0.0
    sector_counts = {}
    held = []
    for p in positions or []:
        tk = p.get('ticker')
        ep = p.get('entry_price')
        stop = p.get('current_stop') or p.get('initial_stop')
        sh = p.get('shares')
        if not (tk and ep and stop and sh):
            continue
        held.append(tk)
        heat_dollars += max(float(ep) - float(stop), 0) * float(sh)
        gross_dollars += float(ep) * float(sh)
        sec = sector_map.get(tk) or 'other'
        sector_counts[sec] = sector_counts.get(sec, 0) + 1
    return {
        'open_heat_pct': round(heat_dollars / account_size * 100, 2) if account_size > 0 else 0.0,
        'open_gross_pct': round(gross_dollars / account_size * 100, 2) if account_size > 0 else 0.0,
        'sector_counts': sector_counts,
        'held_tickers': held,
    }


# ============================================================
# 大盤 regime → 風險乘數（自動化原本要人工執行的減倉紀律）
# ============================================================
def regime_risk_mult(light: str) -> float:
    """
    green 滿倉 1.0 / yellow 減半 0.5 / red 極輕 0.25。
    這是把儀表板原本「叫人手動減倉」的紀律自動化進下注大小，
    而非硬性 regime gate（硬 gate 在回測中被否決，會砍掉修正後 V 轉的續抱）。
    修正期失血是已知弱點 → 用紀律性縮倉降曝險，但不放棄參與。
    """
    return {'green': 1.0, 'yellow': 0.5, 'red': 0.25}.get(light, 0.5)


# ============================================================
# 容量分配：按信心序依次吃「熱度 / gross 名目 / 板塊席次」容量
# ============================================================
def allocate_capacity(cards: list, open_heat_pct: float, open_gross_pct: float,
                      open_sector_counts: dict, account_size: float,
                      max_heat_pct: float, max_gross_pct: float,
                      max_sector_positions: int) -> None:
    """
    就地修改 cards（需已按 conviction 由高到低排序）：最佳信號先吃容量，
    後續信號被「剩餘容量」夾住 → 真正縮倉/歸零，而非僅掛旗標。
    每張卡需含 entry / stop / sector / _desired_shares。寫回:
      suggested_shares, suggested_dollars, trade_risk_pct,
      capacity_capped(被容量夾小), no_room(無容量), capacity_note。
    三道硬上限：組合熱度(停損風險)、gross 名目曝險、同板塊席次。
    """
    run_heat = float(open_heat_pct)
    run_gross = float(open_gross_pct)
    sect = dict(open_sector_counts or {})

    for c in cards:
        entry = float(c['entry'])
        risk_ps = max(entry - float(c['stop']), 0.01)
        want = int(c.pop('_desired_shares', c.get('suggested_shares', 0)))
        sec = c.get('sector') or 'other'

        notes = []
        # 板塊席次硬上限
        if sect.get(sec, 0) >= max_sector_positions:
            want = 0
            notes.append(f'板塊已滿({sect.get(sec, 0)}/{max_sector_positions})')

        # 剩餘容量（$）
        heat_room = max(max_heat_pct - run_heat, 0.0) / 100.0 * account_size   # 可用停損風險
        gross_room = max(max_gross_pct - run_gross, 0.0) / 100.0 * account_size  # 可用名目
        sh_by_heat = int(heat_room / risk_ps) if risk_ps > 0 else 0
        sh_by_gross = int(gross_room / entry) if entry > 0 else 0
        capped = max(min(want, sh_by_heat, sh_by_gross), 0)

        if capped < want and want > 0:
            if capped == sh_by_gross and sh_by_gross <= sh_by_heat:
                notes.append('gross名目封頂')
            elif capped == sh_by_heat:
                notes.append('組合熱度封頂')

        tr = capped * risk_ps / account_size * 100 if account_size > 0 else 0.0
        c['suggested_shares'] = capped
        c['suggested_dollars'] = round(capped * entry, 0)
        c['trade_risk_pct'] = round(tr, 2)
        c['capacity_capped'] = bool(capped < want)
        c['no_room'] = bool(capped <= 0)
        c['capacity_note'] = ' / '.join(notes) if notes else None

        if capped > 0:
            run_heat += tr
            run_gross += capped * entry / account_size * 100
            sect[sec] = sect.get(sec, 0) + 1

    return None


def compute_ext_rank(stocks: dict) -> dict:
    """廣股池橫斷面：每檔對 200MA 乖離的百分位 {ticker: pct 0~100}（最新一日）。"""
    ext = {}
    for tk, df in stocks.items():
        if len(df) < 200:
            continue
        ma200 = float(df['Close'].iloc[-200:].mean())
        if ma200 > 0:
            ext[tk] = float(df['Close'].iloc[-1]) / ma200 - 1
    if not ext:
        return {}
    s = pd.Series(ext)
    pct = s.rank(pct=True) * 100
    return {tk: float(p) for tk, p in pct.items()}
