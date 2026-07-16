"""
core_status.py - 核心(擇時指數)狀態產生器
========================================================================
核心 = 擇時指數(多檔,以SMH為主)。每檔用『自己的』進出場+下注參數,不再用SMH硬碼。
  進場：站上200MA 且 距50MA ≤ 該指數門檻(entry_thr) → 可進(不追高)
  出場：收盤跌破 200MA×該指數緩衝(exit_buf) → 轉現金
  下注：曝險 = clip(budget / 停損距, 0, cap)  ← RiskTarget(取代 inverse-vol)
  不停利：騎乘趨勢直到跌破線(回測證實提早停利砍右尾)。

★ per-index 參數(research_per_index_params.py + optimize_risk_sizing_per_index.py)：
  低波動(SPY/QQQ)門檻緊、出場貼MA200;高波動(SMH/SOXX/XLK)門檻寬、留緩衝防鋸齒。

★★ RiskTarget 倉位(optimize_risk_sizing*.py，2026-06-23 取代 VolTarget)：
  倉位由『離停損線(MA200×exit_buf)的距離』決定,不再由波動決定。
  貼近MA200(便宜/深拉回)→停損短→加倉; 遠離(貴/趨勢老)→停損寬→自動減倉=taper。
  解三盲點: #1(不再恐慌縮倉逆edge) #3(末段自動taper) #6(進場綠燈連動停損距)。
  理論=fixed-fractional risk(每筆風險固定),非曲線擬合。全5標的 Sharpe≥VolTarget、OOS不更差。

★ 恐慌容忍出場(research_exit.py 背書)：跌破但 VIX>30 恐慌中→給3日確認別砍V底。
★ 信用카나리아(research_index_methods.py)：HYG跌破自己200MA=信用示警,提前減/出。

輸出 Web_Dashboard/core_status.json。用法: python core_status.py
"""

import os
import sys
import json
import warnings
from datetime import datetime
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import pandas as pd
import yfinance as yf

from scanner_base import DASHBOARD_DIR

CORE_TICKERS = ['SMH', 'QQQ', 'XLK', 'SOXX', 'SPY']
CANARY_TICKER = 'HYG'        # (legacy) 單一信用哨
CREDIT_TICKERS = ['HYG', 'LQD']   # ★信用 breadth 哨：高收益債 + 投資級債(research_breadth_canary.py #6)
PANIC_TICKER = '^VIX'        # 全市場恐慌計
MA = 200
MA_FAST = 50
PANIC_VIX = 30.0             # VIX 高於此 = 恐慌(啟動容忍出場)
PANIC_DELAY = 3              # 恐慌中跌破,給幾個交易日確認再砍

# ★ 裸價格結構讀數(research_price_action.py,跨 SMH/QQQ/SPY 三指數驗證)——目標=減少犯錯/找對下手點:
#   F4 上方壓力:現價卡在前波高點正下方→期望值明顯較差(3指數一致:藍天格報酬約高一倍)。
#   F5 突破量能:創新高當天『爆量』=短線耗竭別追那根(反教科書,3指數一致);『無量緩破』才是健康續勢。
#   R:R:下檔=到停損線、上檔=到最近前高壓力(藍天=開放)→量化「這是不是好下手點」。
#   鋸齒區:離200MA太近(±CHOP_ZONE_PCT)=剛站上假訊號多(research_entry_timing:剛站上勝率僅52%)→降信心。
#   ⚠️量級誠實:這些是『減少犯錯/挑點』的結構提示,不改 Sharpe 量級(同所有邊際優化);價值在紀律非印鈔。
PIVOT_K = 5                  # fractal pivot:左右各 k 根才確認(PIT,不看未來)
RESIST_LOOKBACK = 120        # 找上方壓力的回看天數
BREAKOUT_WIN = 60            # 創 N 日新高 = 突破
VOL_WIN = 20                 # 相對成交量基準(20 日均量)
BLOWOFF_VOL = 1.5            # 突破當日量 > 此倍 = 爆量耗竭(別追)
QUIET_VOL = 1.0             # 突破當日量 < 此倍 = 無量緩破(健康續勢)
CHOP_ZONE_PCT = 3.0          # 離 200MA ±此% 內 = 鋸齒區(剛站上,訊號可信度低)

# ★ 恐慌 base-rate(2006-2026,5指數 SMH/QQQ/SPY/XLK/SOXX 彙總,跌破200MA+VIX>30 的歷史前瞻報酬)——防手賤砍底:
#   砍在此情境的當下,歷史上 67% 的時候 21 日後更高(中位 +3.3%)、74% 的時候 63 日後更高(中位 +8.3%)。
#   ⚠️這支持『恐慌容忍出場(別砍V底頭3日)』,不是『永不出場』——系統仍會在確認跌破後出場。
PANIC_BASE_RATE_NOTE = ('📊 歷史基準率(跌破200MA+VIX>30,2006-26/5指數):未來21日中位 +3.3%、67%上漲;'
                        '63日中位 +8.3%、74%上漲 → 砍在此刻歷史上約 2/3 是砍早了。看完3日確認再說,別砍V底。')
PANIC_BASE_RATE = {'fwd21_median': 3.3, 'fwd21_up_pct': 67, 'fwd63_median': 8.3, 'fwd63_up_pct': 74,
                   'note': PANIC_BASE_RATE_NOTE}

# ★ 每指數一表統管(進場門檻 / 出場緩衝 / 風險下注 budget / 槓桿上限)
#   entry_thr = 進場「不追高」上限(×50MA)   exit_buf = 停損線(×200MA)
#   budget/cap = RiskTarget 倉位:曝險 = clip(budget / 停損距, 0, cap)
#     budget 由 optimize_risk_sizing_per_index.py 校準(平均曝險對齊原 VolTarget cap1.5)
#     高波動動能股(SMH/SOXX/XLK)budget 較大、停損距也較寬→曝險隨拉回起伏明顯;
#     廣基(QQQ/SPY)較平滑→sizing 影響小(驗證 ΔSharpe≈0,但報酬仍升、無害)。
# credit_k = 該指數的信用門檻(HYG<200MA×credit_k 才砍曝險)。research_xlk_credit.py:
#   半導體/成長(SMH/SOXX/QQQ/SPY)對信用真敏感→k=1.00(跌破就砍,2022保護最佳);
#   XLK含蘋果微軟巨頭=利率/品質驅動,淺信用波動是假警報→k=0.98(只認深破),救回報酬+保住風險。
#   resist_warn = 「頭上壓力」黃燈門檻(剩餘空間≤此才警示;2026-07-17 逐根全史審計,五指數+台股全解剖):
#     QQQ/XLK/SPY=-1(永不警示,改純資訊):全史「壓」各距離事後皆≥「買」(QQQ壓+0% 63日+6.5%/86% vs 買+4.2%;
#       SPY壓全格+4.0~5.2% vs 買+2.3%)=廣基/巨頭指數動能直接碾過前高,「等突破」系統性錯誤。
#     SMH=0.005(只正頂前高+0%才警示):該格21日+1.7%/勝60%=全場最弱;+1~3%≈買。
#     SOXX=0.015(≤1%警示):唯一真的會被壓的——壓≤1% 63日+7.1~7.4% vs 買+12.2%,差距大。
#     台股006208/0050=0.005(只+0%弱,見taiwan_status)。⚠️僅改建議文字,不動進出場/倉位機制。
PARAMS = {
    'SMH':  {'entry_thr': 1.10, 'exit_buf': 0.98, 'budget': 0.213, 'cap': 1.5, 'credit_k': 1.00, 'resist_warn': 0.005},
    'SOXX': {'entry_thr': 1.07, 'exit_buf': 0.99, 'budget': 0.201, 'cap': 1.5, 'credit_k': 1.00, 'resist_warn': 0.015},
    'QQQ':  {'entry_thr': 1.07, 'exit_buf': 1.00, 'budget': 0.199, 'cap': 1.5, 'credit_k': 1.00, 'resist_warn': -1},
    'XLK':  {'entry_thr': 1.10, 'exit_buf': 0.98, 'budget': 0.213, 'cap': 1.5, 'credit_k': 0.98, 'resist_warn': -1},
    'SPY':  {'entry_thr': 1.05, 'exit_buf': 1.00, 'budget': 0.190, 'cap': 1.5, 'credit_k': 1.00, 'resist_warn': -1},
}
DEFAULT_PARAM = {'entry_thr': 1.08, 'exit_buf': 0.98, 'budget': 0.20, 'cap': 1.5, 'credit_k': 1.00}

# 風險旋鈕(資本配置線):同時縮放 budget 與 cap,沿同一效率線滑動,不改 Sharpe。
#   ★Kelly實證(research_costs_kelly.py):數學說可加到2-4x,但那是『樣本無真熊市』的幻覺,別信。
#   紀律上限=1.5(往上=賭牛市續命,真熊市重傷)。建議:保守0.67(MDD-18%)/標準1.0(-26%)/積極1.25(-32%)。
RISK_MULT = 1.0
RISK_MULT = min(RISK_MULT, 1.5)   # 硬上限:防止誤設成 Kelly 級槓桿
STOP_DIST_FLOOR = 0.02   # 停損距下限(剛站上MA200時防爆槓桿,對應回測 clip(lower=0.02))

# ★ vol-timing 下注縮放(research_voltiming_robust.py / research_sizing_deepen.py):
#   牛市(站上200MA 且 MA200上彎)時,cap 隨波動縮放=clip(cap×中位波動/EWMA波動, cap×0.67, cap×1.67)。
#   平靜(波動<中位)→加碼、動盪(崩前兆)→自動縮。信用哨仍在 main 把關(信用壞→曝險照砍)。
#   ★用 EWMA 波動『預測』(非事後 realized):48格掃描 QQQ 48/48、SMH 40/48(EWMA 把 SMH 從 32/48 救活)。
#   誠實:邊際升級(+0.03~0.05 Sharpe)非普適、非最大財富(換效率/淺回撤);SPY 無效不啟用。機制=Moreira-Muir。
VOL_TIMING = {'QQQ', 'SMH'}   # EWMA 後 QQQ+SMH 皆穩健啟用;SPY 無效不啟用
VOL_WIN = 20             # EWMA 波動 span
VOL_MED = 252            # 中位波動窗(基準)
VOL_CAP_LO_MULT = 0.667  # cap 下限 = cap×此(base cap 1.5 → 1.0)
VOL_CAP_HI_MULT = 1.667  # cap 上限 = cap×此(base cap 1.5 → 2.5)

# ★ 信用cushion(research_sizing_deepen.py):vol-timing 再乘一個信用緩衝因子——信用(HYG/LQD)高於自己
#   200MA 的距離越薄→cap 縮越多(崩前信用先轉弱=先降槓桿)。實測=降回撤器(SMH MDD -41.7→-36.2、
#   QQQ -29.5→-26.8),Sharpe 中性、微傷 CAGR。是「讓積極更安全」的旋鈕,可關(CREDIT_CUSHION=False)。
CREDIT_CUSHION = True
CUSHION_MED = 252        # 信用距離中位窗
CUSHION_K = 6.0          # 距離偏離中位 → 因子斜率
CUSHION_LO, CUSHION_HI = 0.8, 1.25   # 因子上下限

# 路線B(輪動)用哪個分數挑最強核心:
#   'voladj' = 126日波動調整動能(最高Sharpe1.37,穩健,預設)
#   'csm'    = 60/120/252多週期平均(最大財富:報酬+43%、OOS同穩,代價-0.08Sharpe)
SELECT_METRIC = 'voladj'

# 信用哨防禦模式(research_breadth_canary.py):
#   'smooth' = HYG+LQD平滑(一壞×50%兩壞×0;Sharpe1.37,穩健,預設)
#   'wealth' = 皆壞才出(只在兩信用都跌破才砍0;報酬+14907%最高,OOS最差0.49稍遜)
CREDIT_MODE = 'smooth'

# 路線B 最低持有期(research_rotation_manual.py:最低持有21交易日≈30日曆,全程+4OOS全面宰制現用)：
#   換進最強核心後至少抱滿 MIN_HOLD_DAYS 才准再換(殺鋸齒、降換手、湊長期稅);
#   但中途跌破200MA(失格/信用砍)仍立刻離場,不受鎖約束。
#   live 用持久化:每次讀上次 core_status.json 的 rotation_hold,比對換倉日。
MIN_HOLD_DAYS = 30


def _trailing_below(close: pd.Series, ma_series: pd.Series, buf_mult: float) -> int:
    """回傳『最近連續幾個交易日收盤都在 200MA×buf_mult 以下』。"""
    below = (close < ma_series * buf_mult).values
    cnt = 0
    for v in reversed(below):
        if v:
            cnt += 1
        else:
            break
    return cnt


def _credit_cushion(credit_closes: dict) -> float:
    """信用cushion因子(HYG/LQD 高於自己200MA的距離 vs 其中位;越薄→<1縮cap、異常強→>1)。PIT trailing。"""
    try:
        dists = []
        for s in credit_closes.values():
            ma = s.rolling(MA).mean()
            dists.append(s / ma - 1)
        cs = sum(dists) / len(dists)
        cmed = cs.rolling(CUSHION_MED).median()
        f = 0.8 + (float(cs.iloc[-1]) - float(cmed.iloc[-1])) * CUSHION_K
        if f != f:
            return 1.0
        return round(min(max(f, CUSHION_LO), CUSHION_HI), 3)
    except Exception:
        return 1.0


def _structure_read(ohlc: pd.DataFrame, last: float, exit_price: float, dist_pct: float,
                    resist_warn: float = 0.03) -> dict:
    """裸價格結構讀數(F4 上方壓力 + F5 突破量能 + R:R + 鋸齒區信心)。
    ohlc 需含 High/Low/Close/Volume。壓力用『已確認』fractal swing high(pivot 到 i+k 才確認 → 無 look-ahead)。"""
    try:
        high = ohlc['High'].values
        close = ohlc['Close']
        vol = ohlc['Volume']
    except Exception:
        return {}
    n = len(high)
    k = PIVOT_K
    if n < 2 * k + 2:
        return {}
    # 回看窗內、已確認的 swing high(i 的左右各 k 根皆不高於它)
    lo = max(k, n - RESIST_LOOKBACK - k)
    piv_highs = [float(high[i]) for i in range(lo, n - k)
                 if high[i] == high[i - k:i + k + 1].max()]
    above = [p for p in piv_highs if p > last]
    resist = min(above) if above else None                    # 最近的上方壓力(最低的前高)
    resi_room = round((resist / last - 1) * 100, 1) if resist else None

    # R:R:下檔到停損線、上檔到壓力(藍天=無壓=開放,不給比值)
    downside = (last - exit_price) / last
    rr = round(((resist - last) / last) / downside, 2) if (resist and downside > 0) else None

    # F5 突破量能:今日是否創 BREAKOUT_WIN 日新高 + 當日相對量
    relvol = float(vol.iloc[-1] / vol.iloc[-VOL_WIN:].mean()) if len(vol) >= VOL_WIN else None
    prior_max = float(close.iloc[-BREAKOUT_WIN - 1:-1].max()) if len(close) > BREAKOUT_WIN else None
    breakout = None
    if prior_max is not None and relvol is not None and last >= prior_max:
        breakout = 'blowoff' if relvol >= BLOWOFF_VOL else ('quiet' if relvol <= QUIET_VOL else 'normal')

    chop = abs(dist_pct) <= CHOP_ZONE_PCT

    notes = []
    if chop:
        notes.append(f'⚠️鋸齒區(離200MA {dist_pct:+.0f}%,剛站上假訊號多、信心低)')
    if resist:
        if resi_room is not None and resi_room <= resist_warn * 100:
            notes.append(f'⚠️頭上前高壓力 ${resist:.2f}(僅 +{resi_room:.0f}%,期望值較差,宜等突破)')
        elif resi_room is not None and resi_room <= 3:
            notes.append(f'前高 ${resist:.2f}(+{resi_room:.0f}%)在上方,此指數該格歷史無壓制力→不擋'
                         + (f',R:R {rr}' if rr else ''))
        else:
            notes.append(f'上方壓力 ${resist:.2f}(+{resi_room:.0f}%'
                         + (f',R:R {rr}' if rr else '') + ')')
    else:
        notes.append('藍天無壓(F4:上檔開放=報酬最高格)')
    if breakout == 'blowoff':
        notes.append(f'🚫爆量突破(量 {relvol:.1f}倍=短線耗竭,別追那根)')
    elif breakout == 'quiet':
        notes.append(f'✅無量緩破(量 {relvol:.1f}倍=健康續勢)')

    return {'resist_price': round(resist, 2) if resist else None,
            'resi_room_pct': resi_room, 'rr_ratio': rr,
            'breakout': breakout, 'breakout_relvol': round(relvol, 2) if relvol is not None else None,
            'chop_zone': chop, 'structure_note': ' · '.join(notes)}


def core_signal(close: pd.Series, vix_last: float | None, ticker: str,
                ohlc: pd.DataFrame | None = None, cred_factor: float = 1.0) -> dict:
    """核心擇時 + 恐慌容忍 + 進場判定 + RiskTarget 倉位,用該指數自己的參數。
    ohlc(可選,OHLCV)→ 加裸價格結構讀數(上方壓力/突破量能/R:R/鋸齒區)輔助下手決策。"""
    p = PARAMS.get(ticker, DEFAULT_PARAM)
    thr, buf = p['entry_thr'], p['exit_buf']
    budget, cap = p['budget'] * RISK_MULT, p['cap'] * RISK_MULT

    ma_series = close.rolling(MA).mean()
    ma = float(ma_series.iloc[-1])
    last = float(close.iloc[-1])
    ma50 = float(close.iloc[-MA_FAST:].mean())
    dist = (last / ma - 1) * 100
    dist50 = (last / ma50 - 1) * 100
    days_below = _trailing_below(close, ma_series, buf)
    panic = vix_last is not None and vix_last > PANIC_VIX

    exit_price = round(ma * buf, 2)          # 停損/出場線(該指數緩衝)
    entry_cap = round(ma50 * thr, 2)         # 進場上限價(超過=追高)
    stop_risk = round((last - exit_price) / last * 100, 1)   # 現在進場的話,停損在下方幾%
    struct = _structure_read(ohlc, last, exit_price, dist,
                             resist_warn=p.get('resist_warn', 0.03)) if ohlc is not None else {}

    # ★ 橫斷面相對強度:多核心皆 risk_on 時,決定該持有哪一個(#4:持有最強)
    #   rs_score = voladj 126日(最高Sharpe1.37,穩健,預設選股)
    #   rs_csm   = 多週期平均(60/120/252日,借鏡 tanish35/Momentum-Investing):
    #             最大財富版,報酬+43%(+8784 vs +6138)、OOS最差還更穩,代價-0.08 Sharpe。
    #             research_rotation_signal.py 驗證;13612W/偏度/12-1 皆測過更差,否決。
    rs_score = None
    if len(close) > 130:
        r126 = float(close.iloc[-1] / close.iloc[-127] - 1)
        vol126 = float(close.pct_change().iloc[-126:].std() * (252 ** 0.5))
        rs_score = round(r126 / vol126, 3) if vol126 > 0 else None
    rs_csm = None
    if len(close) > 256:
        r60 = float(close.iloc[-1] / close.iloc[-61] - 1)
        r120 = float(close.iloc[-1] / close.iloc[-121] - 1)
        r252 = float(close.iloc[-1] / close.iloc[-253] - 1)
        rs_csm = round((r60 + r120 + r252) / 3, 3)

    # ★ RiskTarget 倉位:曝險 = clip(budget / 停損距, 0, cap)。停損短(便宜)→加倉、停損寬(貴)→減倉。
    #   ★vol-timing(僅 QQQ):牛市時 cap 隨波動縮放(平靜加碼/動盪縮);信用哨仍在 main 把關。
    cap_use = cap
    vol_note = None
    if ticker in VOL_TIMING and last >= ma and len(ma_series) > 21 and ma > float(ma_series.iloc[-21]):
        rvs = close.pct_change().ewm(span=VOL_WIN).std() * (252 ** 0.5)   # EWMA 波動預測(勝 realized)
        rv = float(rvs.iloc[-1]); medv = float(rvs.iloc[-VOL_MED:].median())
        if rv > 0 and medv == medv:
            cf = cred_factor if CREDIT_CUSHION else 1.0
            cap_use = round(min(max(cap * medv / rv * cf, cap * VOL_CAP_LO_MULT), cap * VOL_CAP_HI_MULT), 2)
            cush = f' × 信用cushion {cf:.2f}' if (CREDIT_CUSHION and abs(cf - 1.0) > 0.001) else ''
            vol_note = (f'vol-timing:cap {cap:.2f}→{cap_use:.2f}(EWMA波動 {rv*100:.0f}% vs 中位 {medv*100:.0f}%{cush},'
                        f'{"平靜加碼" if cap_use > cap else "動盪/信用薄縮" if cap_use < cap else "持平"})')
    stop_dist_frac = max(stop_risk / 100.0, STOP_DIST_FLOOR)
    expo_raw = round(min(budget / stop_dist_frac, cap_use), 2)

    # ── 持有者：續抱 / 出場 / 恐慌觀察 ──
    if last >= ma:
        state = 'risk_on'
        hold_action = f'續抱;建議曝險 {expo_raw*100:.0f}%(RiskTarget,不停利騎到跌破線)'
        suggested_expo = expo_raw
    elif last < exit_price:
        if panic and days_below < PANIC_DELAY:
            state = 'panic_watch'
            hold_action = (f'恐慌觀察：VIX {vix_last:.0f}>{PANIC_VIX:.0f}、跌破{days_below}日,'
                           f'給{PANIC_DELAY}日確認別砍V底(維持現倉、別加減)  {PANIC_BASE_RATE_NOTE}')
            suggested_expo = None
        else:
            reason = f'(恐慌已連{days_below}日確認)' if panic else ''
            state, hold_action = 'risk_off', f'出場/停損→轉現金{reason};曝險 0%'
            suggested_expo = 0.0
    else:
        state = 'warning'
        hold_action = (f'逼近停損線,收盤跌破即出;貼線停損距僅 {stop_risk:.0f}%'
                       f'→建議曝險 {expo_raw*100:.0f}%(同$風險、停損線就在腳下)')
        suggested_expo = expo_raw

    # ── 空手者：可進場 / 追高等拉回 / 不進 ──
    if last < ma:
        entry_state = 'no_entry'
        entry_action = f'不進(在200MA ${ma:.2f} 之下,等站回)'
    elif last >= entry_cap:
        entry_state = 'extended'
        entry_action = (f'追高,等拉回到 ${entry_cap:.2f} 以下'
                        f'(距50MA +{dist50:.0f}% > 門檻 +{(thr-1)*100:.0f}%)')
    else:
        entry_state = 'can_enter'
        extra = '；VIX高=恐慌綠燈,果斷進' if panic else ''
        entry_action = (f'可進場(距50MA +{dist50:.0f}% ≤ 門檻 +{(thr-1)*100:.0f}%)'
                        f'；停損距 -{stop_risk:.0f}% → 建議曝險 {expo_raw*100:.0f}%{extra}')

    snote = struct.get('structure_note')
    if snote and entry_state in ('can_enter', 'extended'):
        entry_action = entry_action + '  │ 結構:' + snote

    return {'ma200': round(ma, 2), 'ma50': round(ma50, 2), 'close': round(last, 2),
            'dist_pct': round(dist, 2), 'dist50_pct': round(dist50, 2),
            'entry_thr': thr, 'exit_buf': buf, 'budget': round(budget, 3), 'cap': round(cap, 2),
            'exit_price': exit_price, 'entry_cap': entry_cap, 'stop_risk_pct': stop_risk,
            'suggested_expo': suggested_expo, 'rs_score': rs_score, 'rs_csm': rs_csm, 'is_strongest': False,
            'days_below': days_below, 'panic': panic,
            'state': state, 'action': hold_action, 'hold_action': hold_action,
            'cap_used': cap_use, 'vol_note': vol_note,
            'entry_state': entry_state, 'entry_action': entry_action, **struct}


def canary_signal(credit_closes: dict) -> dict:
    """信用 breadth 哨(借鏡 Keller DAA,research_breadth_canary.py #6 驗證最優)：
    HYG(高收益債)+LQD(投資級債)各看是否站上自己200MA → 健康比例=平滑縮倉乘數。
    一個壞→曝險×50%、兩個壞→×0。比單一HYG:Sharpe1.27→1.37、2022 -9%→-4.5%、報酬+15%。"""
    assets = {}
    healthy = 0
    for tk, close in credit_closes.items():
        ma = float(close.iloc[-MA:].mean()); last = float(close.iloc[-1])
        ok = last >= ma
        healthy += 1 if ok else 0
        assets[tk] = {'close': round(last, 2), 'ma200': round(ma, 2),
                      'dist_pct': round((last / ma - 1) * 100, 2), 'ok': ok}
    n = len(credit_closes) or 1
    frac = healthy / n
    bad = [tk for tk, a in assets.items() if not a['ok']]
    if frac >= 1.0:
        note = '信用健康(HYG+LQD 皆站上200MA) → 曝險×100%'
    elif frac > 0:
        note = f'信用部分示警({"/".join(bad)} 跌破200MA) → 曝險×{frac:.0%}(平滑減碼)'
    else:
        note = '信用全示警(HYG+LQD 皆跌破200MA) → 曝險×0(全出)'
    hyg = assets.get('HYG', next(iter(assets.values())))
    return {'health': round(frac, 3), 'n_healthy': healthy, 'n_total': n, 'assets': assets,
            'credit_ok': frac >= 1.0,
            'state': 'credit_on' if frac >= 1.0 else ('credit_half' if frac > 0 else 'credit_off'),
            'note': note,
            'close': hyg['close'], 'ma200': hyg['ma200'], 'dist_pct': hyg['dist_pct']}


def combine(primary: dict, canary: dict) -> dict:
    """合併核心 + 信用 breadth + 恐慌容忍。信用全壞→risk_off;部分壞→平滑減碼警示。"""
    core_state = primary['state']
    health = canary.get('health', 1.0)
    credit_off = health <= 0          # 全壞才算硬 risk-off
    credit_partial = 0 < health < 1   # 部分壞=平滑減碼

    if core_state == 'panic_watch':
        if credit_off:
            return {'state': 'risk_off', 'action': '出場：恐慌跌破 + 信用全示警(雙殺,不再容忍)'}
        return {'state': 'panic_watch',
                'action': '🟠 恐慌容忍：核心跌破但VIX高+信用尚健康→給3日確認,別砍在V底'}

    if core_state == 'risk_on' and health >= 1.0:
        return {'state': 'risk_on', 'action': '抱住(滿核心倉)：核心與信用皆健康'}
    if core_state != 'risk_on' and credit_off:
        return {'state': 'risk_off', 'action': '出場：核心跌破200MA + 信用全示警(雙確認)'}
    if credit_off:
        return {'state': 'credit_warning',
                'action': '🔴 信用全示警(HYG+LQD皆跌破200MA)：清倉、不新增倉,等信用站回(這是「不買」)'}
    if credit_partial:
        return {'state': 'credit_warning',
                'action': f'🟠 信用部分示警：核心健康、仍可進場,但全體曝險上限砍到 ×{health:.0%}'
                          f'——是「減碼半倉」不是「不能買」(下方每筆建議量已含此減碼)'}
    return {'state': 'core_warning', 'action': '核心跌破200MA → 出場(信用尚健康)'}


def compute_rotation_hold(out: dict, today_strongest, prev: dict | None, today_str: str) -> dict:
    """路線B 最低持有期狀態機(research_rotation_manual.py 最低21交易日)。
    讀上次的 rotation_hold,套『鎖滿 MIN_HOLD_DAYS 才准換、跌破立刻離場』:
      - 前任仍合格(risk_on且未被信用砍)且未滿鎖→續抱前任(忽略今日更強者);
      - 前任滿鎖→可換到今日最強(換則重設換倉日);
      - 前任失格(跌破/信用砍)→強制離場,改持今日最強並重設鎖。"""
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
        if days >= MIN_HOLD_DAYS:                       # 鎖已開,可換今日最強(換則重設換倉日)
            if today_strongest and today_strongest != held and eligible(today_strongest):
                held, since, days = today_strongest, today_str, 0
    else:                                                # 無前任 或 前任失格→重新進場最強
        held = today_strongest if (today_strongest and eligible(today_strongest)) else None
        since, days = today_str, 0

    # 鎖定狀態一律由「持有天數」決定:剛換進(days=0)→鎖;抱滿→開(可換,但若仍最強就續抱)
    locked = bool(held) and days < MIN_HOLD_DAYS
    lock_left = max(0, MIN_HOLD_DAYS - days) if held else 0
    forced_out = bool(prev_tk) and not eligible(prev_tk)
    return {'ticker': held, 'since': since, 'days_held': days, 'locked': locked,
            'lock_days_left': lock_left, 'today_strongest': today_strongest,
            'min_hold_days': MIN_HOLD_DAYS, 'forced_out_prev': forced_out}


def _intraday_tentative() -> tuple[bool, str]:
    """判斷現在是否美股盤中(訊號基於日收盤 → 盤中=暫定,別看盤中殺盤犯錯)。純時間判斷、無資料相依。
       美股常規時段 09:30–16:00 ET。回傳 (是否暫定, 說明)。"""
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo('America/New_York'))
    except Exception:
        return False, ''
    if now_et.weekday() >= 5:                       # 週末 → 用上一個收盤,非暫定
        return False, ''
    open_t = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    if open_t <= now_et < close_t:
        return True, ('⏳ 盤中暫定：以下用『當下未收盤價』計=雜訊。美股 04:00(台灣)收盤才算數;'
                      '沒破停損線就別動手,別被盤中殺盤騙去犯錯。')
    return False, ''


def daily_verdict(out: dict, combined: dict | None, rotation_hold: dict,
                  canary: dict | None, intraday: bool) -> str:
    """把 5 指數 + 信用 + 輪動壓成一行『今日核心該做什麼』,降 over-trading / 讀太細。"""
    if not out:
        return '今日核心動作:無資料'
    if canary and canary.get('health', 1) <= 0:
        return '🔴 今日核心動作【清倉/不進場】:信用全示警(HYG+LQD 皆跌破200MA),等信用站回。'
    held = rotation_hold.get('ticker') if rotation_hold else None
    seg = []
    if held and held in out:
        d = out[held]
        expo = d.get('suggested_expo')
        exs = f"{expo*100:.0f}%" if expo is not None else '維持'
        seg.append(f'持有 {held}(曝險 {exs})')
        if rotation_hold.get('locked'):
            seg.append(f'鎖定中(還 {rotation_hold.get("lock_days_left")} 日才准換)')
        if d.get('resi_room_pct') is not None and d.get('resi_room_pct') <= 3:
            seg.append(f'⚠️{held}頭上前高壓力僅 +{d["resi_room_pct"]:.0f}%,空手宜等突破')
        elif d.get('chop_zone'):
            seg.append(f'⚠️{held}在鋸齒區(離200MA近),信心低')
    else:
        seg.append('空手(無核心站上200MA 或 信用砍0)')
    if combined and combined.get('state') == 'credit_warning':
        seg.append('信用部分示警 → 全體曝險減半')
    core = '今日核心動作【' + ' · '.join(seg) + '】其餘無動作,沒破停損線就別動手。'
    return ('⏳ ' + core) if intraday else core


def main():
    tickers = CORE_TICKERS + CREDIT_TICKERS + [PANIC_TICKER]
    print(f"📥 下載核心指數 + 信用breadth(HYG+LQD) + 恐慌計 ({'/'.join(tickers)}, 2y)...")
    raw = yf.download(' '.join(tickers), period='2y',
                      interval='1d', progress=False, group_by='ticker')

    vix_last = None
    try:
        vix_s = raw[PANIC_TICKER]['Close'].dropna()
        if len(vix_s) > 0:
            vix_last = float(vix_s.iloc[-1])
    except Exception:
        pass

    # 信用cushion因子(給 vol-timing;用 HYG/LQD 的 200MA 距離)
    cred_factor = 1.0
    try:
        cc = {tk: raw[tk]['Close'].dropna() for tk in ('HYG', 'LQD') if tk in CREDIT_TICKERS}
        if cc:
            cred_factor = _credit_cushion(cc)
    except Exception:
        pass

    out = {}
    for tk in CORE_TICKERS:
        try:
            s = raw[tk]['Close'].dropna()
            if len(s) >= MA:
                out[tk] = core_signal(s, vix_last, tk, ohlc=raw[tk].dropna(subset=['Close']),
                                      cred_factor=cred_factor)
        except Exception:
            pass

    canary = None
    try:
        credit_closes = {}
        for tk in CREDIT_TICKERS:
            s = raw[tk]['Close'].dropna()
            if len(s) >= MA:
                credit_closes[tk] = s
        if credit_closes:
            canary = canary_signal(credit_closes)
    except Exception:
        pass

    primary = out.get('SMH') or out.get('QQQ') or (next(iter(out.values())) if out else None)
    primary_tk = 'SMH' if 'SMH' in out else ('QQQ' if 'QQQ' in out else (next(iter(out), None)))
    combined = combine(primary, canary) if (primary and canary) else None

    # ★ 橫斷面:多核心皆 risk_on 時挑 RS 最強的「該持有哪一個」(#2假分散→#4持有最強,別跑5個重複計時器)
    rs_key = 'rs_csm' if SELECT_METRIC == 'csm' else 'rs_score'
    ron = {tk: d for tk, d in out.items()
           if d['state'] == 'risk_on' and d.get(rs_key) is not None}
    strongest = max(ron, key=lambda tk: ron[tk][rs_key]) if ron else None
    if strongest:
        out[strongest]['is_strongest'] = True

    # ★ 信用 breadth 平滑哨接進曝險(research_breadth_canary.py #6):HYG+LQD 健康比例×曝險。
    #   一個壞→×50%、兩個壞→×0。比單一HYG:Sharpe1.27→1.37、2022 -9→-4.5%、報酬+15%。
    #   平滑減碼避免單一誤殺、又抓得到真危機;信用領先股市去槓桿,蓋過一切(即使核心仍站200MA)。
    if canary is None:
        ch = 1.0
    elif CREDIT_MODE == 'wealth':
        ch = 0.0 if canary['health'] <= 0 else 1.0   # 皆壞才出,否則滿
    else:
        ch = canary['health']                          # 平滑:健康比例
    if ch < 1.0:
        for tk, d in out.items():
            if d.get('suggested_expo') is not None:
                d['suggested_expo'] = round(d['suggested_expo'] * ch, 2)
            d['credit_mult'] = ch
            if ch <= 0:
                d['credit_cut'] = True
                d['hold_action'] = '🔴 信用全示警(HYG+LQD皆跌破200MA)→曝險砍0,等信用站回再進'
                d['action'] = d['hold_action']
                d['entry_state'] = 'no_entry'
                d['entry_action'] = '信用全示警期間不進場(等HYG/LQD站回200MA)'
            else:
                d['hold_action'] = d['hold_action'] + f'  ⚠️信用部分示警→曝險×{ch:.0%}'
                d['action'] = d['hold_action']
                if d.get('entry_state') == 'can_enter':
                    d['entry_action'] = d['entry_action'] + f'(信用部分示警,曝險×{ch:.0%})'

    # ★ 路線B 最低持有期(最低21交易日≈30日):讀上次 json 的換倉日,鎖滿才准換。
    path = os.path.join(DASHBOARD_DIR, 'core_status.json')
    prev_hold = None
    try:
        with open(path, encoding='utf-8') as f:
            prev_hold = json.load(f).get('rotation_hold')
    except Exception:
        pass
    today_str = datetime.now().strftime('%Y-%m-%d')
    rotation_hold = compute_rotation_hold(out, strongest, prev_hold, today_str)
    # 標記實際持有(鎖定下可能≠今日最強);取消舊 is_strongest 顯示衝突由前端處理
    if rotation_hold.get('ticker') and rotation_hold['ticker'] in out:
        out[rotation_hold['ticker']]['is_held'] = True

    # ★ 盤中 guard(擋盤中手賤)+ 今日單一結論(降 over-trading)
    intraday_flag, intraday_note = _intraday_tentative()
    verdict = daily_verdict(out, combined, rotation_hold, canary, intraday_flag)

    data = {
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'intraday_tentative': intraday_flag, 'intraday_note': intraday_note,
        'daily_verdict': verdict, 'panic_base_rate': PANIC_BASE_RATE,
        'ma_window': MA, 'panic_vix': PANIC_VIX, 'panic_delay': PANIC_DELAY,
        'vix': round(vix_last, 2) if vix_last is not None else None,
        'primary': primary_tk, 'risk_mult': RISK_MULT, 'strongest': strongest,
        'rotation_hold': rotation_hold,
        'recommended_split': {'A_hold_smh': 80, 'B_rotate': 20},
        'alloc_modes_note': ('配置模式(research_core_satellite.py/confirm_refinements.py):'
                             '🛡️純A=省事(CAGR+38.6/MDD-35);⚖️A+B各半=最穩(Sharpe1.45/MDD-26);'
                             '🚀A60/B40×1.35=最大財富(CAGR+50.8/MDD-34,同回撤多賺+12%/年)。'),
        'route_note': ('★核心衛星實證(research_core_satellite.py)推翻舊「純A唯一解」:'
                       'A+B混合Sharpe(1.45)>純A(1.37),把混合加槓桿到與純A同回撤(-34%)→CAGR反超(+50.8 vs +38.6%)。'
                       '三種選法:不加槓桿求省事→純A;不加槓桿求穩→A+B各半(回撤-26%最穩);'
                       '要最大財富能扛-34%→A60/B40整組×1.3~1.4(同回撤多賺一截,槓桿仍鎖1.5)。'),
        'rotation_manual_note': ('★輪動操作手冊(research_rotation_manual.py):月換是甜蜜點;'
                                 '最低持有21日(買進至少抱一個月才准換、跌破200MA仍立刻離場)全程+4OOS皆不更差還小贏'
                                 '=免費降換手+降稅。遲滯帶/稅緩衝/鎖更久 都實測更差,別用。'),
        'credit_leadtime_note': ('★信用哨領先性實證(research_credit_leadtime.py):HYG領先SPY中位+7交易日、'
                                 '顯著回撤7/10被±10日內預警(真領先);但單一HYG跌破88%是假警報(很吵)'
                                 '→正是為何用HYG+LQD平滑(breadth確認過濾假警報),非單一HYG一刀砍0。'
                                 '2018Q4/2025關稅信用落後=利率驅動回撤信用非主因(對照XLK credit_k=0.98只認深破)。'),
        'leverage_note': ('槓桿RISK_MULT上限1.5:Kelly說2-4x是『樣本無真熊市』的幻覺別信。'
                          '保守0.67(MDD-18%)/標準1.0(-26%)/積極1.25(-32%)。'),
        'discipline_note': ('真edge=熊市躲現金+不手賤砍底(擇時相對買持alpha僅+0.08)。'
                            '殺盤是雜訊:只看收盤、只在跌破停損線才動手,沒破線=續抱、今天別動。'),
        'take_profit_note': ('不設『價格停利目標』(回測證實提早停利砍右尾);但 RiskTarget 會按風險『連續調倉』'
                             '——越貴(離MA200越遠)抱越少,這是按風險減碼,不是價格停利。'),
        'sizing_note': ('倉位=RiskTarget:曝險=clip(budget/停損距,0,cap),取代波動率目標。'
                        '貼近MA200(便宜)加倉、遠離(貴)自動taper。風險旋鈕 RISK_MULT 沿資本配置線縮放。'),
        'strongest_note': ('5個核心擇時訊號86%同向(假分散);多核心皆risk_on時,持有RS最強的單一個'
                           '(走查驗證≈硬抱SMH報酬),別同時跑5個重複計時器。'),
        'params_note': '每個指數用自己校準的進場門檻/出場緩衝/風險budget(非SMH硬碼)',
        'cores': out,
        'canary': canary,
        'combined': combined,
    }
    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    path = os.path.join(DASHBOARD_DIR, 'core_status.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    emoji = {'risk_on': '🟢', 'risk_off': '🔴', 'warning': '🟡', 'panic_watch': '🟠'}
    entry_emoji = {'can_enter': '🟢', 'extended': '🟡', 'no_entry': '🔴'}
    vix_str = f"{vix_last:.1f}" if vix_last is not None else "N/A"
    print(f"\n  恐慌計 VIX = {vix_str} ({'🔴恐慌中' if vix_last and vix_last > PANIC_VIX else '🟢平靜'})")
    if intraday_note:
        print(f"\n  {intraday_note}")
    print(f"\n  👉 {verdict}")
    if vix_last is not None and vix_last > PANIC_VIX:
        print(f"  {PANIC_BASE_RATE_NOTE}")
    print(f"\n  核心擇時狀態 (每指數自校參數 + 恐慌容忍VIX>{PANIC_VIX:.0f}給{PANIC_DELAY}日):")
    for tk, d in out.items():
        below = f" [跌破{d['days_below']}日]" if d['days_below'] > 0 else ""
        ex = f"{d['suggested_expo']*100:.0f}%" if d['suggested_expo'] is not None else "維持"
        crown = ' 👑最強' if d.get('is_strongest') else ''
        print(f"   {emoji[d['state']]} {tk:5}{crown} ${d['close']:.2f} (200MA {d['dist_pct']:+.1f}% / 50MA {d['dist50_pct']:+.1f}%)"
              f"  RS{d['rs_score'] if d['rs_score'] is not None else '—'}"
              f"  參數:進場≤+{(d['entry_thr']-1)*100:.0f}% 出場×{d['exit_buf']:.2f} budget{d['budget']}{below}")
        print(f"          🏠 持有→ {d['action']}")
        print(f"          💵 空手→ {entry_emoji[d['entry_state']]} {d['entry_action']}")
        if d.get('structure_note'):
            print(f"          📐 結構→ {d['structure_note']}")
        print(f"          📊 RiskTarget 建議曝險 {ex} = clip(budget {d['budget']} / 停損距 {d['stop_risk_pct']:.0f}%, 0, cap {d.get('cap_used', d['cap']):.2f})")
        if d.get('vol_note'):
            print(f"          🌊 {d['vol_note']}")
        print(f"          🛑 停損線 ${d['exit_price']:.2f}(現價進場停損距 -{d['stop_risk_pct']:.0f}%) · 進場上限 ${d['entry_cap']:.2f} · 不停利")
    if canary:
        ce = '🟢' if canary['health'] >= 1 else ('🟠' if canary['health'] > 0 else '🔴')
        astr = ' · '.join(f"{tk}{'🟢' if a['ok'] else '🔴'}{a['dist_pct']:+.1f}%"
                          for tk, a in canary['assets'].items())
        print(f"\n  信用 breadth 哨: {ce} 健康 {canary['n_healthy']}/{canary['n_total']} "
              f"→ 曝險×{canary['health']:.0%}  [{astr}]")
    if strongest:
        sd = out[strongest]
        ex = f"{sd['suggested_expo']*100:.0f}%" if sd.get('suggested_expo') is not None else '維持'
        print(f"\n  👑 多核心皆站上時,持有最強的單一個：{strongest} (RS {sd[rs_key]}, "
              f"建議曝險 {ex}) — 別同時跑5個重複計時器(86%同向)")
    rh = rotation_hold
    if rh.get('ticker'):
        if rh['locked']:
            lk = f"🔒 鎖定中(已抱{rh['days_held']}日,還要{rh['lock_days_left']}日滿{MIN_HOLD_DAYS}日才准換)"
            extra = f";今日最強={rh['today_strongest']}(滿鎖再考慮換)" if rh['today_strongest'] != rh['ticker'] else ""
        else:
            lk = f"🔓 已滿{MIN_HOLD_DAYS}日,可換(今日最強={rh['today_strongest']})"
            extra = ""
        fo = " ⚠️前任跌破被強制換出" if rh.get('forced_out_prev') else ""
        print(f"\n  🅱️ 路線B 實際持有(最低{MIN_HOLD_DAYS}日鎖):{rh['ticker']}({rh['since']}進場) {lk}{extra}{fo}")
    else:
        print(f"\n  🅱️ 路線B 實際持有:空手(無核心站上200MA或信用砍0)")
    if combined:
        ce = emoji.get(combined['state'],
                       {'credit_warning': '🟠', 'core_warning': '🟡'}.get(combined['state'], '⚪'))
        print(f"\n  👉 合併判斷(核心{data['primary']} + 信用 + 恐慌)：{ce} {combined['action']}")
    print(f"💾 {path}")


if __name__ == '__main__':
    main()
