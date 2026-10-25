"""v2_status.py - 新版(v2)每日決策資料產生器
========================================================================
不重算行情:直接讀 core_status.py / taiwan_status.py 已經產出的 core_status.json、taiwan_status.json
(同一套 200MA + 信用哨 + 各檔門檻),只負責把「該持有哪些、各多少權重」這一層換成 2026-09-23
獨立驗證後的做法,並把每一條規則的數據依據一起輸出,讓前端可以逐條顯示。

持有方式(HOLD_METHOD):
  'rotation' = 現行:單押 RS 最強一檔(持滿30日才准換)
  'ew'       = 等權:候選池每檔各自擇時,各占 1/N,不合格的那份放現金
  'topn'     = 前 N 名等權
判定來源:research_rot_verify_p1.py / research_rot_verify_p2.py(規格 research_cache/rot_refutation.md)

輸出 Web_Dashboard/v2_status.json。用法: python v2_status.py
"""
import os, sys, json, warnings
from datetime import datetime
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

from scanner_base import DASHBOARD_DIR

# ── 持有方式:兩種都算,預設值由 2026-09-23 事前登錄驗證的判定決定 ──
#   research_cache/rot_verify_summary.md(規格 rot_refutation.md;美股、台股分開判定)
#   美股:等權 vs 單押最強「統計上打平」(Δ超額Sharpe +0.013、p=0.41)→ 依規格「沒有證據就不改」,預設維持單押;
#         但頁面可切成等權(同波動報酬一樣、回撤淺 10 個百分點)。
#   台股:等權在主池與長樣本池都過關(Δ+0.21/+0.23、同波動年化 +4.3pp)→ 預設改等權。
# ★2026-09-24 Jason 決定:兩邊都用等權。
#   台股本來就是數據判定的結果;美股是「統計上打平」時的自主選擇(等權回撤淺 10 個百分點、
#   而且不依賴無法證實的挑選能力),頁面仍保留切回單押的選項。
DEFAULT_METHOD = {'us': 'ew', 'tw': 'ew'}
TOPN = {'us': 3, 'tw': 3}

# ── 官方績效:頁面上「現行設定」的數字一律從這裡取,禁止在別處手打(2026-10-24 v2 UX 稽核 P1)──
#   重算來源:research_b6_options_table.py(等權+RiskTarget+信用哨,不再平衡)/ research_b9_final.py 槓桿階梯;
#   2026-10-24 稽核以 scratchpad chk_expo.py / chk2.py / audit_expo.py 重跑,逐位一致。
#   其他設定(單押、1 倍、無信用哨…)的數字可以出現,但必須附設定標籤。
OFFICIAL = {
    # ★2026-10-25 R1(research_b14_execution_lag.py):改用「隔天開盤成交」——實際做得到的最快執行方式。
    #   舊數字(t 日收盤成交)美股 17.3%/−34.2%、台股 22.4%/−18.2%;台股舊數字還偷看了一天
    #   (美股信用哨的 t 日收盤比台股 t 日收盤晚約 14 小時)。
    'us': {'setup': '5 檔等權 + RiskTarget + 信用哨,隔天開盤成交', 'period': '2002-05 ~ 2026-09',
           'x1': {'cagr': 16.7, 'mdd': -35.4, 'sharpe': 0.80},
           'x125': {'cagr': 19.7, 'mdd': -42.8, 'sharpe': 0.79},
           'margin_note': '美股 ×1.0 就會用到融資:每檔最多買到這一份的 1.5 倍,回測中約一半的日子(52.7%)總曝險超過 100%。',
           'hindsight_note': '5 檔標的是 2026 年回頭挑的(事後挑選),未來期望值應往下調;'
                             '在 1 倍、無信用哨的設定下差 2.9 個百分點(13.5%→10.6%),現行設定下的幅度未回測。'},
    'tw': {'setup': '4 檔等權 + 信用哨(固定 1 倍),隔天開盤成交', 'period': '2013-04 ~ 2026-09',
           'x1': {'cagr': 19.6, 'mdd': -19.2, 'sharpe': 1.43},
           'x125': {'cagr': 23.7, 'mdd': -23.8, 'sharpe': 1.40},
           'margin_note': '台股 ×1.0 不融資;×1.25 需要融資,回測只算 1% 利息,實際利率以券商為準。',
           'hindsight_note': ''},
}
METHOD_EVIDENCE = {
    'us': {'default': 'ew', 'verdict': '三種打平 — 選等權是為了分散',
           'detail': '(以下為 2026-09-23 驗證時的設定:1 倍、未含信用哨)'
                     '主判定池(SMH/SOXX/QQQ/XLK/SPY,2002-2026):等權 − 單押最強 = 超額Sharpe +0.013、p=0.41、'
                     '同波動年化 +0.03pp → 兩者沒有差別;前 N 名曲線平坦(1檔0.71/2檔0.67/3檔0.69/4檔0.68/5檔0.68)'
                     '→「挑選訊號無法判定」。但無後見之明的 14 檔產業池:等權 +0.264、p=0.024、三段全正、'
                     '同波動年化 +4.3pp,明顯站在等權那邊;「挑選 alpha」t 值僅 1.66(不顯著)。'
                     '回撤:單押 −36.1% vs 等權 −26.0%。',
           'options': {'rotation': '單押 RS 最強一檔(原版做法,持滿 30 個日曆天才准換);RS = 近半年報酬÷波動,越高越強;約32筆/年',
                       'ew': '5 檔各 1/5,各自看自己的 200MA;現行設定下回撤和單押差不多(−34.2% vs −33.4%),交易約102筆/年',
                       'single': '只持有 QQQ,不換;和等權統計上打平(Sharpe 0.75 vs 0.83,不顯著),交易最少(約19筆/年)'}},
    'tw': {'default': 'ew', 'verdict': '四種打平 — 預設 4 檔等權',
           'detail': '(以下為 2026-09-23 驗證時的設定:1 倍、未含信用哨)'
                     '主判定池(0050/0052/006208/0051,2013-2026):等權 − 單押最強 = 超額Sharpe +0.210、p=0.038、'
                     '同波動年化 +4.28pp、三段有兩段為正,2倍成本/統一緩衝/RiskTarget倉位/21個換倉日全部同方向;'
                     '長樣本池(0050/0052/0051,2009起)+0.229、p=0.015(Holm 校正後 0.045 過關)。'
                     '前 N 名曲線單調上升:1檔1.06 → 2檔1.16 → 3檔1.23 → 4檔1.22。'
                     '同群換倉(0050↔006208)占 25%,換完之後 21 日平均 −0.19% = 白付成本。'
                     '⚠️主池顯著性 Holm 校正後 0.114,差一點沒過 → 證據等級「中等」。',
           'options': {'ew': '4 檔各占一份,各自看自己的 200MA。注意:006208 和 0050 追蹤同一指數,等於台灣 50 占一半',
                       'ew3': '3 檔等權:去掉和 0050 同指數的 006208;績效幾乎相同(Sharpe 1.41 vs 1.43),交易較少(約48筆/年)',
                       'single': '只持有 0050,不換;和等權打平(Sharpe 1.38 vs 1.43),交易最少(約16筆/年)',
                       'rotation': '單押 RS 最強一檔(原版做法,持滿 30 個日曆天才准換);Sharpe 1.30,較低但不顯著'}},
}


DECISION_VERDICT = {
    "method_us": "⚖️ 三者打平,沒有證據偏好任何一個:等權−單押 Δ−0.016(p=0.56)、等權−QQQ +0.074(p=0.20)、單押−QQQ +0.090(p=0.14)。預設的等權沒問題;若想省事,只持有 QQQ 交易最少(19 筆/年)。",
    "method_tw": "⚖️ 四種打平(隔天開盤成交,research_b15):3 檔 −0.013、只持有 0050 −0.046、單押最強 −0.130 [−0.32,+0.07],都不顯著。舊假設下「單押最站不住」的結論,修正偷看一天後不再顯著。預設 4 檔等權 Sharpe 最高但差距在雜訊內;交易最少的「只持有 0050」成本最低。",
    "lev_us": "⚖️ 數據無法決定(隔天開盤成交:Sharpe 0.80 vs 0.79)= 純風險偏好。×1.25 多年化 3.4pp,但回撤 −34%→−41%、最差12個月 −21.5%→−27.1%、最長水下 3.8→5.7 年(research_b9)。另:美股標的是事後挑的,未來期望值應往下調。",
    "lev_tw": "⚖️ 數據無法決定(隔天開盤成交:Sharpe 1.43 vs 1.40)= 純風險偏好。×1.25 多年化 4.1pp,回撤 −19%→−24%、最差12個月 −9.6%→−12.0%、水下期都 1.2 年。⚠️回測只算 1% 融資成本;實際融資利率通常更高(以券商為準),扣息後實際多賺會少於 4.1pp(幅度未回測)。",
    "sat_tw": "✅ 0% 最站得住:配 20% 正2,超額 Sharpe 顯著變差(−0.060,90%信賴區間 [−0.10,−0.02] 整段小於 0);換到年化 +3.2pp、回撤 −18.9%→−22.2%(現行設定、live 閘門、隔天開盤成交)。",
    "credit_us": "⚖️ 打平(皆壞才清倉 − 平滑 +0.004,p=0.46)。差別只在交易量。",
    "conflict": "⚖️ 打平。用等權或只持有一檔時不用選:那兩種本身就是「續抱到跌破停損線」,這題只在選「單押最強」時才有意義。(美股 −0.001 p=0.51;台股主池換掉略好、長樣本續抱略好 p=0.061,方向相反。)"
}

LEV_TICKER = '00631L.TW'
# 每個市場「應該有」的核心標的:下載缺一檔時,等權份數仍以這個為準(不准靜默變成 1/4),缺的那份標示資料缺失
EXPECTED_CORES = {'us': ['SMH', 'QQQ', 'XLK', 'SOXX', 'SPY'], 'tw': ['0050.TW', '0052.TW', '006208.TW', '0051.TW']}
SINGLE_ANCHOR = {'us': 'QQQ', 'tw': '0050.TW'}

# ── 決策清單:每條都給正反兩邊的實測數字,選了就直接影響上面的「今天怎麼做」──
#    數字來源:research_q4_choices.py(A 槓桿階梯 / D 正2接法)、research_rot_verify_p1.py(持有方式)
DECISIONS = [
    {'key': 'method_us', 'title': '美股持有方式(預設:5 檔等權)',
     'note': '統一基準重算(research_b6_options_table.py,2002-2026,含信用哨、RiskTarget、不再平衡):'
             '三者 Sharpe 0.75~0.84,統計上都分不出高下(等權 vs 單押 p=0.56)。差別在交易量與集中度。'
             '⚠️美股回測有後見之明:5 檔是 2026 年回頭挑的。在 1 倍、無信用哨的設定下,事後挑 13.5% vs 站在每年當下挑 10.6%'
             '(research_b4);現行設定下的下修幅度未回測,但方向是往下。',
     'options': [
         {'v': 'ew', 'label': '5 檔等權', 'nums': 'CAGR 17.3% · 回撤 −34.2% · Sharpe 0.83 · 約102筆/年'},
         {'v': 'rotation', 'label': '單押最強(原版的持有方式)', 'nums': 'CAGR 18.9% · 回撤 −33.4% · Sharpe 0.84 · 約32筆/年'},
         {'v': 'single', 'label': '只持有 QQQ(最省事)', 'nums': 'CAGR 15.5% · 回撤 −33.5% · Sharpe 0.75 · 約19筆/年'}]},
    {'key': 'method_tw', 'title': '台股持有方式(預設:4 檔等權)',
     'note': '統一基準重算(2013-2026,含信用哨、固定1倍、不再平衡、隔天開盤成交)。只持有 0050 和等權打平(Sharpe 1.38 vs 1.43),'
             '交易只有 1/4。'
             '小資金請注意:最低手續費 NT$20/筆,單筆金額小時實際費率會高於回測假設的 0.10%;額外拖累未精確計算。',
     'options': [
         {'v': 'ew', 'label': '4 檔等權', 'nums': 'CAGR 19.6% · 回撤 −19.2% · Sharpe 1.43 · 約64筆/年'},
         {'v': 'ew3', 'label': '3 檔等權(去掉重複的 006208)', 'nums': 'CAGR 19.6% · 回撤 −19.1% · Sharpe 1.41 · 約48筆/年'},
         {'v': 'single', 'label': '只持有 0050(最省事)', 'nums': 'CAGR 20.3% · 回撤 −20.0% · Sharpe 1.38 · 約16筆/年'},
         {'v': 'rotation', 'label': '單押最強(原版的持有方式,1 倍)', 'nums': 'CAGR 20.2% · 回撤 −19.6% · Sharpe 1.30 · 約27筆/年'}]},
    {'key': 'lev_us', 'title': '美股要開多少槓桿?(選了會套用到「今天怎麼做」的金額)',
     'note': '2002-2026,5 檔等權+RiskTarget+信用哨(現行設定)。×1.0 與 ×1.25 的 Sharpe 幾乎一樣(0.83 vs 0.82),'
             '差別只有「賺多少 vs 跌多深」。⚠️×1.0 本身就會用到融資(RiskTarget 每檔上限 1.5 倍)。'
             '×0.5、×0.75 在現行設定下還沒回測,所以不列數字。',
     'options': [
         {'v': '0.5', 'label': '×0.5(保守)', 'nums': '現行設定下未回測'},
         {'v': '0.75', 'label': '×0.75', 'nums': '現行設定下未回測'},
         {'v': '1', 'label': '×1.0(預設,已含 RiskTarget 融資)', 'nums': 'CAGR 16.7% · 回撤 −35.4% · Sharpe 0.80(隔天開盤成交)'},
         {'v': '1.25', 'label': '×1.25(積極)', 'nums': 'CAGR 19.7% · 回撤 −42.8% · Sharpe 0.79(隔天開盤成交)'}]},
    {'key': 'lev_tw', 'title': '台股要開多少槓桿?(選了會套用到「今天怎麼做」的金額)',
     'note': '2013-2026,4 檔等權+信用哨(現行設定)。×1.0 不融資;×1.25 需融資,回測只算 1% 利息,'
             '要自己確認券商利率。×0.5、×0.75 在現行設定下還沒回測,所以不列數字。',
     'options': [
         {'v': '0.5', 'label': '×0.5(保守)', 'nums': '現行設定下未回測'},
         {'v': '0.75', 'label': '×0.75', 'nums': '現行設定下未回測'},
         {'v': '1', 'label': '×1.0(預設,不融資)', 'nums': 'CAGR 19.6% · 回撤 −19.2% · Sharpe 1.43(隔天開盤成交)'},
         {'v': '1.25', 'label': '×1.25(需融資)', 'nums': 'CAGR 23.7% · 回撤 −23.8% · Sharpe 1.40(隔天開盤成交)'}]},
    {'key': 'sat_tw', 'title': '進階:要不要配槓桿 ETF 00631L(元大台灣50正2)當衛星?結論:不建議',
     'note': '2015-08~2026-09 同期比較,現行設定(核心 4 檔等權+信用哨,每月拉回目標比例,隔天開盤成交);正2 用 live 規則:'
             '母股 0050 站上 200MA 且正2 自己沒跌破停損線才持有,也乘信用乘數。加正2衛星報酬較高但 Sharpe 較差。'
             '(2026-10-25:舊版數字的核心比例算錯,已修正重算。)',
     'options': [
         {'v': '0', 'label': '不配(數據最佳)', 'nums': '純核心:CAGR 21.9% · 回撤 −18.9% · 超額Sharpe 1.53'},
         {'v': '0.2', 'label': '衛星 20%', 'nums': 'CAGR 25.1% · 回撤 −22.2% · 超額Sharpe 1.47(顯著較差)'},
         {'v': '0.4', 'label': '衛星 40%', 'nums': 'CAGR 28.2% · 回撤 −25.4% · 超額Sharpe 1.41(顯著較差)'},
         {'v': '1', 'label': '全押正2(最兇)', 'nums': 'CAGR 36.7% · 回撤 −34.8% · 超額Sharpe 1.28(顯著較差)'}]},
    {'key': 'credit_us', 'title': '美股信用哨要用哪一版?(台股那版已驗證有效,不動)',
     'note': '2026-09-24 重驗:美股信用哨整體效果很小(ΔSharpe +0.08、p=0.17,校正後不顯著),但 2022 年少跌 21pp。'
             '兩個都跌破時兩版都清倉;差別只在只有一個跌破時(平滑版減半、另一版不動)。'
             '以下數字為等權、1 倍、未含 RiskTarget 的設定。',
     'options': [
         {'v': 'smooth', 'label': '平滑減碼(現行:一壞砍半)', 'nums': 'CAGR 12.5% · 回撤 −24.4% · Sharpe 0.78 · 約100筆/年'},
         {'v': 'wealth', 'label': '皆壞才清倉(較簡單)', 'nums': 'CAGR 13.3% · 回撤 −24.6% · Sharpe 0.78 · 約74筆/年'}]},
    {'key': 'conflict', 'title': '價格在「停損線 ~ 200MA」之間時,要續抱還是換掉?',
     'note': '兩套規則在這一格會給相反指示;回測顯示兩種版本差異 ≈ 0(美股 −0.029、台股 −0.043、長樣本 +0.008)。'
             '沒有數據上正確的一方——選一個口徑,之後遇到就照選的做,頁面會在發生時標記。',
     'options': [
         {'v': 'hold', 'label': '續抱到跌破停損線(交易少)', 'nums': '台股長樣本 Sharpe 0.73 vs 0.72(差 +0.008)'},
         {'v': 'switch', 'label': '不在200MA之上就換掉(嚴格)', 'nums': '美股 0.69 vs 0.66(差 −0.029);台股 1.05 vs 1.01'}]},
]


# ── 新版 vs 原版差異(每一條都要能指到可重跑的腳本)──
DIFF = [
    {'item': '美股「別追高」門檻', 'old': 'SMH/XLK +10%、SOXX/QQQ +7%、SPY +5%(離50MA)', 'new': '不變',
     'why': '到場測試:等拉回較好 32年中27年 → 這條本來就對(research_q2_lights_audit.py)'},
    {'item': '台股「別追高」門檻', 'old': '0050 +6%、其他 +8%、00757 +12%', 'new': '全部 +12%',
     'why': '舊門檻等拉回平均反而少賺 0.7~1.8%;+12% 前半4年中3年、後半6/6年成立,5檔全正(q2b/q2c)'},
    {'item': '美股「空手先別進」', 'old': '曝險<50%(等於離停損 ≥38~43%)→ 25年從沒觸發過', 'new': '離停損線 ≥20%',
     'why': '只看沒追高的日子:等拉近再買 +0.66%、14年中12年較好(q2d)'},
    {'item': '台股「空手先別進」', 'old': '曝險<50%(離停損 ≥16~19%)', 'new': '平時取消;只在信用示警期間才有',
     'why': '離停損12~18%時「等」平均少賺3~4%、勝率<40% = 被反證;信用示警版本14年中11年較好(q2d/q2e)'},
    {'item': '台股倉位', 'old': 'RiskTarget:離均線越遠押越少', 'new': '在場時固定 1 倍',
     'why': 'RiskTarget 在台股全期5/5檔、2018後5/5檔 Sharpe 都輸固定1倍,年化少2~5%,回撤沒更小(q3b)'},
    {'item': '持有方式', 'old': '單押 RS 最強一檔', 'new': '美股、台股都等權',
     'why': '台股 +0.210(p=0.038)/長樣本 +0.229(p=0.015)(1 倍、未含信用哨;現行設定 p=0.076/0.020);美股三種打平,選等權是為了分散、不依賴無法證實的挑選能力(rot_verify)'},
    {'item': '台股標的', 'old': '0050/0052/006208/0051/00757', 'new': '0050/0052/006208/0051(移除 00757)',
     'why': '00757=美股FANG+,和美股區塊重複;單獨加入效果未達門檻(+0.094,p=0.165),9個候選也沒有一檔達標(b3_tw_replace)'},
    {'item': '台股 V底救援', 'old': '沒有(只有美股 SMH/SOXX)', 'new': '啟用',
     'why': '+0.137、p=0.014、校正後 0.056、三段全正(b2_overlays)'},
    {'item': '台股恐慌容忍', 'old': 'VIX>30 跌破給3日', 'new': '關閉',
     'why': '−0.051、回撤惡化 4.3pp(b2_overlays)'},
    {'item': '美股 vol-timing', 'old': 'QQQ/SMH 啟用', 'new': '關閉',
     'why': '+0.008(p=0.374)但交易 70→133 筆/年(b2_overlays)'},
    {'item': '美股倉位', 'old': 'RiskTarget(平均約1.4倍槓桿)', 'new': '不變,但頁面明講這是槓桿',
     'why': '跟同平均曝險的固定槓桿比 ΔSharpe 僅 +0.01~0.03 → 多賺的是槓桿本身,不是挑時機(q13/q3b)'},
]
# ── 最新發現(有價值、已落地)與研究待辦(Jason 2026-09-24:「把最新資訊有價值跟要研究的上到網站上」)──
FINDINGS = [
    {'date': '2026-10-25', 'title': '台股回測偷看了一天;修正後官方績效 22.4% → 19.6%,結論都沒翻轉',
     'body': '台股的信用哨是美股 HYG/LQD/SOXX,但美股收盤比台股收盤晚約 14 小時;舊回測在台股 t 日收盤就用美股 t 日收盤 = 偷看一天。'
             '改成做得到的「隔天開盤成交」:台股 19.6%/−19.2%(舊 22.4%/−18.2%),美股 16.7%/−35.4%(舊 17.3%/−34.2%,只是執行延後的影響)。'
             '重跑台股結論:信用哨仍有效(+0.16,p=0.016,舊宣稱 +0.36);V底救援仍有效(+0.12);正2衛星仍顯著較差;'
             '持有方式四種打平(舊說的「單押最站不住」不再顯著);信用黃燈變弱(等待平均 +0.63% → +0.34%)。',
     'status': '官方績效已改;規則不變', 'script': 'research_b14_execution_lag.py / research_b15_tw_feasible.py'},
    {'date': '2026-10-24', 'title': '美股三個新濾網(市場寬度/殖利率曲線/雙動能)全部不採用',
     'body': '事前寫死 6 道判準後才跑。加在現行系統(200MA+信用哨)之上,全部讓 Sharpe 變差:寬度 −0.055、'
             '殖利率倒掛 −0.081、倒掛後轉正 −0.156、大盤雙動能 −0.129、逐檔雙動能 −0.118;無後見之明的 14 檔池也全部為負。'
             '改拿「只有 200MA」當基礎,它們也取代不了信用哨:信用哨 +0.104,其餘三個 −0.10 左右;'
             '2022 年信用哨只虧 −6.0%,其餘 −29%~−34%。→ 美股保護就是 200MA + 信用哨,不再加濾網。',
     'status': '不採用', 'script': 'research_b12_us_filters.py / research_b12b_substitute.py'},
    {'date': '2026-10-24', 'title': '待決策項目逐一算 p 值:只有兩項有明確答案',
     'body': '① 台股「單押最強」最站不住(等權勝,p=0.076/0.020);② 正2衛星 0% 最好(配20% Sharpe 顯著變差;2026-10-25 修正核心比例後重算仍成立,'
             '信賴區間整段<0)。其餘(美股持有方式、台股等權/3檔/0050、槓桿、信用哨版本、續抱/換掉)全部打平'
             '→ 依交易成本與風險偏好選。每一項的證據已寫在決策清單裡。',
     'status': '已標示在決策清單', 'script': 'research_b9_final.py A / research_b10_satellite_p.py'},
    {'date': '2026-10-24', 'title': '最嚴格關卡 Deflated Sharpe:台股等權沒通過(0.75/0.85,門檻0.90)',
     'body': '扣掉「試了十幾種方法才挑到」的運氣後,台股等權勝過單押的信心是 75~85%,不到 90%。方向一致、'
             '證據中等,不改變決定但不宣稱它是定論。美股 0.33(兩者本來就打平)。',
     'status': '已更新證據等級', 'script': 'research_b9_final.py D'},
    {'date': '2026-10-24', 'title': 'XLK 的 credit_k 從來沒生效過 → 已移除',
     'body': 'PARAMS 裡定義了 XLK 只認深破(0.98),但程式從沒用到。實作出來測 0.97/0.98/0.99,'
             '和 1.00 比 ΔSharpe −0.007~−0.009(p≈0.75)= 沒效果 → 不實作,刪掉參數與誤導說明。',
     'status': '已落地', 'script': 'research_b9_final.py B'},
    {'date': '2026-10-24', 'title': '事前選池:用流動性挑也贏不了事後挑的 5 檔',
     'body': '每年挑過去一年成交金額最大的 5 檔:年化 9.6%/Sharpe 0.66,對事後挑的 US5 落後 0.093(p=0.21)。'
             '和「過去36月報酬前5」(10.6%)一樣,再次確認美股回測約有 3~4pp 後見之明;沒有可採用的事前規則。',
     'status': '不採用;維持期望值下修', 'script': 'research_b9_final.py C'},
    {'date': '2026-10-24', 'title': '個股衛星沒有可證實的 edge(連有利偏差都只跟 QQQ 擇時打平)',
     'body': '用今天的 S&P500 500 檔回測 2007-2026(有生存者偏差,結果只會偏好看):衛星+大盤濾網 年化 15.8%/'
             'Sharpe 0.73/回撤 −30.8%;QQQ 200MA 擇時 12.4%/0.73/−26.3%;對 SPY 擇時 Δ+0.086(p=0.35,不顯著)。'
             '沒有大盤濾網時 2008 年 −34%、回撤 −45%。舊研究 Sharpe 1.9~2.2 的測試期(2023-2026)剛好是衛星最好的'
             '幾年(每年 +26~65%)→ 那是挑到好窗口,不是 edge。要做真正的驗證需要歷史成分股資料(付費資料)。',
     'status': '建議:衛星最多小比例,不要當主力;v2 不納入', 'script': 'research_b8_satellite.py'},
    {'date': '2026-10-24', 'title': '進取模式(TQQQ/SOXL):規則不一致無害,但回撤可達 −85%',
     'body': 'live「三哨任一壞就賣光」vs 核心「平滑減碼」:Sharpe 打平(Δ +0.008/−0.006),live 版最差年較輕 → 維持。'
             '信用哨對槓桿 ETF 最關鍵:2022 年 TQQQ 有哨 +4.8% vs 沒哨 −43.2%,SOXL +2.8% vs −72.5%。'
             '⚠️即使全部保護都開,2008-2026 最大回撤 TQQQ −59%、SOXL −85%(合成3倍,與真實對帳相關 0.999/0.997)。',
     'status': '規則不動;風險數字已標示', 'script': 'research_b7_aggressive.py'},
    {'date': '2026-10-24', 'title': '美股回測被後見之明灌水約每年 3 個百分點',
     'body': '現用 5 檔是回頭挑的:事後挑年化 13.5%;站在每年當下用「過去 36 個月報酬前 5」挑只有 10.6%,'
             '差 0.12 Sharpe(p=0.045)。零挑選的 14 檔產業池只有 7.6%。(以上為 1 倍、無信用哨的設定)'
             '→ 美股未來期望值應往下調;現行設定(隔天開盤成交 16.7%)下的下修幅度未回測。'
             '好消息:事前法在 2017 年後自己也會選到 QQQ/SMH/SOXX/XLK,差距主要集中在 2009、2023、2025。',
     'status': '已標示在決策清單', 'script': 'research_b4_pool_hindsight.py'},
    {'date': '2026-10-24', 'title': '等權不需要再平衡;只持有一檔寬基指數和等權打平',
     'body': '每月/每季/每年/從不再平衡,Sharpe 差距都 ≤0.02(全不顯著),「從不」少下 27~36% 的單。'
             '只持有 0050:台股 Sharpe 1.58 vs 等權 1.60,交易 16 vs 64 筆/年;只持有 QQQ:0.75 vs 0.83(不顯著)。'
             '→ 真正傷績效的是「輪動換來換去」,不是「只持有一檔」。',
     'status': '已落地:頁面改成不再平衡;持有方式新增「只持有一檔」選項', 'script': 'research_b5_friction.py / research_b6_options_table.py'},
    {'date': '2026-10-24', 'title': '台股小資金的最低手續費(2026-10-25 更正)',
     'body': '最低手續費 NT$20/筆:單筆金額小時,實際費率會高於回測假設的 0.10%/邊。先前寫的「10 萬 ≈ 每年 2.0%、'
             '30 萬 ≈ 0.67%」把回測已扣的成本又算了一次,而且假設的單筆大小和不再平衡的實際做法不符 → 撤回;'
             '額外拖累要看實際單筆金額,未精確計算。',
     'status': '數字撤回', 'script': 'research_b5_friction.py'},
    {'date': '2026-09-24', 'title': '台股信用哨是真 edge;美股不顯著但 2022 年少跌 21pp',
     'body': '等權基礎重驗:台股 ΔSharpe +0.359(p<0.001,多重檢定後仍成立),2015/2018/2022 三個壞年份'
             '從 −4.9/−4.9/−9.8% 翻成 +1.4/+2.8/+2.3%;美股 +0.080(p=0.172):不顯著,但「不顯著≠沒用」——24 年裡只有 3~4 次危機能證明它,樣本太少;實際上 2022 年(升息空頭,200MA 反應慢)只用 200MA −24.3%,加信用哨 −3.4%,少跌 21pp;2008 −8.4%→−4.8%、2015 −5.0%→+2.1%(research_b11_us_layers.py)。',
     'status': '已落地:兩邊都保留(2026-10-24 更正美股的說法)', 'script': 'research_b1_credit_canary.py'},
    {'date': '2026-09-24', 'title': '台股新增 V 底救援、關閉恐慌容忍;美股關閉 vol-timing',
     'body': 'V底救援台股 +0.137(p=0.014、校正後 0.056、三段全正、年化 19.3→22.1%);'
             '恐慌容忍台股 −0.051、回撤惡化 4.3pp;vol-timing 美股 +0.008(p=0.374)但交易 70→133 筆/年。',
     'status': '已落地', 'script': 'research_b2_overlays.py'},
    {'date': '2026-09-24', 'title': 'p 值重算:只有「台股別追高 12%」撐過整體多重檢定',
     'body': '黃燈類改用逐年 bootstrap(重抽年份,消掉樣本重疊的假顯著):台股別追高12% p<0.001;'
             '美股別追高 p=0.130、美股空手先別進 p=0.200 → 方向對但不顯著,已降級標示。'
             'QQQ 跌深上2x 單看 p=0.042,但是從 300 組裡挑的,校正後 0.42 = 多重檢定陷阱。',
     'status': '已更新各規則的證據強度', 'script': 'research_q5_pvalues.py'},
    {'date': '2026-09-24', 'title': '台股移除 00757,不需要替代標的',
     'body': '9 個候選當第5檔(同期比較):最好的 00692 只有 +0.014,00757 自己 +0.094(p=0.165)'
             '→ 沒有一檔達標。0050/006208 相關 0.896,去掉一檔績效一樣但少 25 筆交易/年(見決策清單)。',
     'status': '已落地:台股核心 4 檔', 'script': 'research_b3_tw_replace.py'},
    {'date': '2026-09-23', 'title': '新版 vs 原版(台股):年化 13.2% → 19.3%、回撤 −33.7% → −20.3%(1 倍、未含信用哨)',
     'body': '等權+固定1倍 vs 單押輪動+RiskTarget:ΔSharpe +0.375(p=0.041);長樣本池 +0.391(p=0.011)。'
             '拆成兩步:取消 RiskTarget 13.2%→18.7%(主要的報酬來源);單押→等權 Sharpe 1.05→1.26(主要的穩定度來源)。'
             '加上信用哨後是 22.4%/−18.2%(同樣是當天收盤成交;改成隔天開盤成交後 19.6%/−19.2%)。美股兩版相同。',
     'status': '已落地', 'script': 'research_q4_choices.py'},
    {'date': '2026-09-23', 'title': 'RS「挑最強」沒有可證實的選股能力',
     'body': '四個池子的挑選 alpha t 值 1.66/0.29/0.59/0.11 全 <2;12 個候選挑選機制校正後全部沒過。'
             '分散(等權)才是穩定的來源。',
     'status': '已落地:兩市場都改等權', 'script': 'research_rot_verify_p1.py / p2.py'},
]
# 進行中/待補的驗證(2026-10-24 UX 稽核的開放問題;只列題目,不放未驗證的數字)
RESEARCH_PENDING = [
    {'item': '前瞻追蹤 12 個月', 'why': '規則定案(2026-09-24)後的真實表現;天數見「前瞻追蹤」'},
    {'item': '美股真正不融資版本(每檔上限 100%)', 'why': '已決定要提供;回測跑完、有數字後才上架成選項'},
    {'item': '槓桿 ×0.5 / ×0.75', 'why': '現行設定下還沒回測,選項暫時關閉'},
    {'item': '台股信用黃燈的完整範圍', 'why': '驗證只涵蓋離停損 ≤ 2×budget 的日子;live 已收窄到這個範圍,更遠的範圍要不要也啟用待測'},
    {'item': '後見之明在現行設定下的下修幅度', 'why': '1 倍、無信用哨下是 2.9pp;現行設定下未回測'},
    {'item': '最低手續費的實際拖累', 'why': '先前的數字重複計算已撤回;要用實際單筆金額重算'},
    {'item': '組合層「含黃燈、V底救援、R3」的完整績效', 'why': '官方績效只含 200MA + 信用哨(美股另含 RiskTarget)'},
    {'item': '全參數使用率掃描', 'why': '確認沒有第二個像 credit_k 那樣「定義了但程式沒用到」的參數'},
    {'item': '美股停損緩衝(0.98~1.00)重驗', 'why': '沿用舊研究的逐檔校準,本輪沒有重驗'},
]
RESEARCH_TODO = [
    {'item': '台股結論用隔天開盤成交重跑', 'why': '持有方式→四種打平(單押不再顯著較差);V底救援仍有效(+0.12);正2衛星仍較差;信用黃燈變弱(+0.34%)', 'status': '✅ 2026-10-25 完成'},
    {'item': '隔日執行的落差(R1)', 'why': '隔天開盤成交:美股 17.3→16.7%;台股 22.4→19.6%(舊數字偷看一天);台股信用哨修正後仍有效(+0.16,p=0.016)', 'status': '✅ 2026-10-25 完成,官方績效已改'},
    {'item': '個股衛星整套重驗', 'why': '上限測試:最好也只跟 QQQ 擇時打平;乾淨驗證需歷史成分股資料(付費)', 'status': '✅ 2026-10-24 完成(上限測試)'},
    {'item': '進取模式(TQQQ/SOXL)', 'why': '規則不一致無害;但最大回撤 −59%/−85%', 'status': '✅ 2026-10-24 完成'},
    {'item': '美股「池子」本身', 'why': '後見之明約每年 3 個百分點;事前挑法 2017 後會自己選到同樣標的 → 不換池,但調低期望', 'status': '✅ 2026-10-24 完成'},
    {'item': '事前選池', 'why': '報酬法 10.6%、流動性法 9.6%,都輸事後挑的 13.5% → 不採用,期望值下修', 'status': '✅ 2026-10-24 完成'},
    {'item': '等權的實務摩擦', 'why': '再平衡不用做;只持有一檔指數與等權打平;最低手續費的拖累數字重複計算,已撤回', 'status': '✅ 2026-10-24 完成(10-25 更正)'},
    {'item': 'XLK 的信用門檻 credit_k', 'why': '參數從沒生效;實測無效果 → 移除', 'status': '✅ 2026-10-24 完成'},
    {'item': '未跑完的驗證關卡', 'why': 'DSR 台股 0.75/0.85 未達 0.90;檢查池候選層與主池一致;bootstrap 恢復 2000', 'status': '✅ 2026-10-24 完成'},
    {'item': '前瞻紙上追蹤 12 個月', 'why': '2002-2026 研究時已全部看過;凍結日 2026-09-24 之後的新資料才是真考驗', 'status': '✅ 2026-10-24 上線(每天自動,見「前瞻追蹤」分頁)'},
    {'item': '信用哨重驗', 'why': '—', 'status': '✅ 2026-09-24 完成'},
    {'item': 'vol-timing / 恐慌容忍 / V底救援重驗', 'why': '—', 'status': '✅ 2026-09-24 完成'},
    {'item': '所有結論補算 p 值', 'why': '—', 'status': '✅ 2026-09-24 完成'},
    {'item': '00757 替代標的', 'why': '—', 'status': '✅ 2026-09-24 完成(不需替代)'},
]

LIMITS = [
    '訊號全部用<b>收盤價</b>,盤中數字只是暫定;22:30 那班是美股盤中,決策看隔天早上 09:00 那班。',
    '績效數字用<b>隔天開盤成交</b>(實際做得到的方式)。舊研究原本假設「訊號當天收盤就成交」,台股還因為信用哨用到比台股收盤晚約 14 小時的美股收盤而偷看一天;'
    '台股的持有方式、正2衛星、V底救援、信用黃燈已全部重跑(結論都沒翻轉)。美股的選項比較和舊研究數字仍是舊假設,約偏高 0.6pp。',
    '頁面上的績效數字都<b>不含</b>黃燈規則(別追高、空手先別進)。',
    '回測期間:美股 2002-2026、台股 2013-2026(0050/0052/0051 可回到 2009)。台股樣本短,結論強度本來就低於美股。',
    '2026-09-24 以前的資料在研究過程中已經全部看過;之後的真實表現見「前瞻追蹤」,至少看滿 12 個月再下結論。',
    '美股 5 檔標的是事後挑的,回測報酬有後見之明,未來期望值應往下調(現行設定下的幅度未回測)。',
    '信用哨 2026-09-24 已重驗:台股證據強;美股統計上不顯著(危機樣本少),但 2022 年少跌 21pp。',
    '成本假設:美股 0.05%、台股 0.10%/單邊(已含賣出證交稅的粗估)。所有判定都在 2 倍成本下重跑過,方向不變。',
    '進取模式(TQQQ/SOXL)、每月定投、個股衛星不在 v2;槓桿倍數在「設定」的決策清單裡選。',
]


# ── 規則依據(每條規則的門檻怎麼來、有沒有回測、效果多大)──
#    給前端逐條顯示,不准只寫結論不寫數字。
RULE_EVIDENCE = {
    # (規則, 證據原文, 一句白話, 證據等級)——前台先顯示白話+等級,原文收在「看細節」(2026-10-24 UX 稽核 P3-1)
    'trend': ('進場:收盤 ≥ 200日均線;出場:收盤跌破停損線(= 200MA×緩衝)才全部轉現金',
              '緩衝各檔不同(SPY/QQQ 1.00、SOXX 0.99、SMH/XLK 0.98;台股 0.98~0.99),來自舊研究的逐檔校準,本輪未重驗。'
              '進出場線不同是刻意的(遲滯):跌破 200MA 但還在停損線之上時,已持有的續抱、空手的不進。'
              '擇時本身的價值:SPY 回撤 −55%→−21%、SMH −68%→−31%、0050 −34%→−21%(research_q1)。',
              '價格在長期均線之上才持有;跌破停損線就出場。大空頭的回撤明顯變淺(SPY −55%→−21%、0050 −34%→−21%)。',
              '擇時:強;緩衝數值:弱(未重驗)'),
    'credit': ('信用哨健康比例 = 曝險乘數(已持有的部位也照乘)',
               '⚠️2026-10-25 更正(research_b14_execution_lag.py):b1 的台股數字把美股 t 日收盤用在台股 t 日收盤,'
               '但美股收盤晚約 14 小時 = 偷看一天。改成隔天開盤成交後:台股信用哨 +0.164 [+0.03,+0.33]、p=0.016,仍有效但小一半;'
               '回撤 −20.3→−19.2%。以下為原始(偷看)數字:'
               '2026-09-24 重驗(research_b1_credit_canary.py,等權基礎):'
               '台股 ΔSharpe +0.359、p<0.001、Holm 校正後仍過、三段全正,回撤 −20.3→−17.7%;'
               '美股 +0.080(p=0.172):不顯著,但「不顯著≠沒用」——24 年裡只有 3~4 次危機能證明它,樣本太少;實際上 2022 年(升息空頭,200MA 反應慢)只用 200MA −24.3%,加信用哨 −3.4%,少跌 21pp;2008 −8.4%→−4.8%、2015 −5.0%→+2.1%(research_b11_us_layers.py)。',
               '債券市場(高收益債、投資級債)通常比股市先出問題;它們跌破長期均線時,股票部位跟著減碼。'
               '台股用隔天開盤成交重驗(修正偷看一天)後仍有效:超額 Sharpe +0.16(p=0.016),但比原本宣稱的 +0.36 小一半。',
               '台股:中~強(修正偷看後);美股:中'),
    'extended_us': ('離 50 日均線 ≥ 門檻 → 別追高(等拉回)',
                    '門檻來自各檔逐檔校準(SMH/XLK +10%、SOXX/QQQ +7%、SPY +5%)。'
                    '2026-09-23 到場測試驗證:等拉回較好 67%、32年中27年、平均 +0.43%;'
                    '但 2026-09-24 用更保守的逐年 bootstrap 重算 p=0.130(不顯著)→ 方向對、強度弱。',
                    '短期漲太多時先等拉回再買;方向對,但統計上不顯著,直接買也和回測一致。',
                    '弱'),
    'extended_tw': ('離 50 日均線 ≥ +12% → 別追高(等拉回)',
                    '2026-09-23 掃 3%~15% 一整排門檻後選出的平台區中間值:+12% 等待較好 71%、平均 +1.08%,'
                    '前半(<2018)4年中3年、後半 6/6 年、5檔全正;+10% 不成立。'
                    'p<0.001,是所有結論中唯一撐過整體多重檢定的一條。舊門檻 +6~8% 反而有害(p=0.955)。',
                    '短期漲太多(離 50 日線 12% 以上)時先等拉回;這是所有規則裡證據最強的一條,建議照做。',
                    '強'),
    'far_us': ('沒追高但離停損線 ≥ 20% → 空手先別進',
               '舊版用「曝險<50%」= 離停損 38~43%,美股 25 年一天都沒觸發過(等於沒這條)。'
               '改用距離掃描:只看沒被別追高擋下的日子,離停損 ≥20% 時等拉近再買平均 +0.66%、14年中12年較好;'
               '但逐年 bootstrap p=0.200(不顯著)→ 保留是因為方向一致且無害。',
               '離停損線太遠時,空手的人先等價格拉近;只影響新買進,已持有的照常持有。統計上不顯著。',
               '弱'),
    'far_tw': ('信用示警期間,離停損線 > budget×信用健康度÷0.5 → 空手先別進',
               '台股平時版本被反證(離停損 12~18% 時等待平均少賺 3~4%、勝率<40%)已移除;'
               '只有信用示警期間的版本有支撐:舊對齊下等待平均 +0.63%、勝64%、14年中11年;'
               '2026-10-25 修正偷看一天後(research_b15)變弱:平均 +0.34%、勝58%、14年中10年,前半5/6、後半5/8。'
               '驗證只涵蓋「離停損 ≤ 2×budget」的日子,所以 live 也只在這個範圍內啟用(2026-10-25 收窄)。',
               '信用示警期間,離停損線太遠的話空手先別進;已持有的照信用乘數減碼。方向對,但修正偷看一天後效果只剩約一半。',
               '弱~中'),
    'r3': ('別追高的日子裡,若 200MA 上彎 且 VIX 低於自己20日均(恐慌退燒)→ 可照系統曝險買進',
           '不等拉回、直接用系統算出的曝險買進(不是額外加碼)。'
           '依據:R3 成立時「等拉回」和「立即買」幾乎沒差(平均 −0.19%),所以等待沒有價值;'
           'R3 不成立時等拉回較好(+0.41%)。台股同現象但逐年不一致 → 台股沒有這條。',
           '雖然漲多了,但趨勢向上、恐慌在退,這時等拉回沒有好處,可以照常買。',
           '弱~中'),
    'vbottom': ('系統空手 + 距252日高回撤>15% + VIX從≥40尖峰退燒至75% → 半倉救援進場,停損 −7%',
                '2026-10-25 隔天開盤成交重驗(research_b15):台股 +0.116 [+0.03,+0.23]、單尾 p=0.009,年化 19.6→21.7%,仍成立。'
                '台股 2026-09-24 新增時(舊假設):等權基礎 ΔSharpe +0.137、p=0.014、Holm 後 0.056、三段全正,'
                'CAGR 19.3→22.1%(research_b2_overlays.py)。美股同測僅 +0.025,維持 SMH/SOXX 限定。',
                '大跌後恐慌指數從高點明顯退燒時,先用半倉搶反彈,停損設 −7%。美股只用在 SMH/SOXX。',
                '台股:中~強'),
    'sizing_us': ('曝險 = min(budget ÷ 離停損距離, 150%) × 信用乘數',
                  '實測 ≈ 同平均槓桿的固定倍數(ΔSharpe +0.01~0.03)→ 多賺的是槓桿本身。'
                  '上限 150% 代表會用到融資:現行設定回測中約一半的日子(52.7%)總曝險超過 100%,平均借款約淨值的 19%,最高 56%。'
                  'vol-timing 已於 2026-09-24 關閉(+0.008、p=0.374,但交易 70→133 筆/年)。',
                  '離停損線越近買越多(停損時虧的錢固定),每檔最多買到這一份的 1.5 倍,所以會用到融資。',
                  '中(效果等同固定槓桿)'),
    'sizing_tw': ('曝險 = 100% × 信用乘數(固定1倍)',
                  'RiskTarget 在台股逐檔 5/5、2018後 5/5 都較差(組合層 p=0.367),2026-09-23 改固定1倍。',
                  '在場時就買滿這一份(不融資),信用示警時照比例減。',
                  '中'),
}


def _trace(d, mkt, health):
    """回傳這一檔今天「每一條規則的檢查結果」:條件、當下數值、門檻、過不過、依據。"""
    import core_status as C
    close, ma = d.get('close'), d.get('ma200')
    dist, dist50 = d.get('dist_pct'), d.get('dist50_pct')
    thr_pct = round((d.get('entry_thr', 1) - 1) * 100, 1)
    sr = d.get('stop_risk_pct')
    out = []
    ok_trend = (dist is not None and dist >= 0)
    st = d.get('state')
    stop = d.get('exit_price')
    if ok_trend:
        v1, lvl1 = '在趨勢中', 'ok'
    elif st in ('warning', 'panic_watch'):
        v1, lvl1 = f'跌破 200MA,但還在停損線 {stop} 之上 → 已持有續抱、空手不進', 'part'
    else:
        v1, lvl1 = f'收盤跌破停損線 {stop} → 已持有賣出、空手不進', 'bad'
    out.append({'id': 'trend', 'name': '① 趨勢:站上200MA?',
                'cond': f"進場:收盤 ≥ 200MA(={ma});出場:收盤 < 停損線(={stop})",
                'now': f"收盤 {close}(離200MA {dist:+.1f}%)" if dist is not None else '—',
                'ok': ok_trend, 'level': lvl1, 'verdict': v1})
    out.append({'id': 'credit', 'name': '② 信用哨:今天的曝險上限',
                'cond': '健康的哨兵數 ÷ 全部哨兵 = 曝險乘數',
                'now': f"健康比例 {health:.0%} → 每筆量 ×{health:.0%}" if health is not None else '—',
                'ok': (health or 0) >= 1,
                'level': 'ok' if (health or 0) >= 1 else ('part' if (health or 0) > 0 else 'bad'),
                'verdict': ('全額' if (health or 0) >= 1 else
                            (f'減量 ×{health:.0%}(已持有的也減到這個比例)' if (health or 0) > 0
                             else '清倉:已持有全部賣出、空手不買'))})
    # ★直接用數字判斷(不看 entry_state):信用全示警時 entry_state 會被整批改成 no_entry,
    #   用它回推會讓「其實已經追高」的標的誤顯示成 ✔ 沒追高(2026-09-24 SOXX 實例:+7.39% > 7%)。
    ext = (dist50 is not None and dist50 >= thr_pct)
    out.append({'id': 'extended_' + mkt, 'name': '③ 別追高:離50MA夠近嗎?',
                'cond': f"離50MA < +{thr_pct}%", 'now': f"離50MA {dist50:+.1f}%" if dist50 is not None else '—',
                'ok': not ext, 'verdict': '沒追高' if not ext else f'超過門檻 → 等拉回到 {d.get("entry_cap")} 以下'})
    if mkt == 'us':
        lim = C.STOP_TOO_FAR_PCT
        far_ok = (sr is not None and sr < lim)
        now = f"離停損線 {sr}%" if sr is not None else '—'
    else:
        lim = round((d.get('budget') or 0) * (health or 1) / 0.5 * 100, 1)
        lim_hi = round((d.get('budget') or 0) / 0.5 * 100, 1)     # 驗證涵蓋的上限(D1:超過不啟用)
        far_ok = not (0 < (health or 1) < 1 and sr is not None and lim < sr <= lim_hi)
        now = f"離停損線 {sr}%" + ('' if 0 < (health or 1) < 1 else '(信用滿血時這條不啟用)') + \
            (f'(超過 {lim_hi}% 不在驗證範圍內,這條不啟用)' if (sr is not None and sr > lim_hi and 0 < (health or 1) < 1) else '')
    out.append({'id': 'far_' + mkt, 'name': '④ 空手先別進:離停損線太遠嗎?',
                'cond': (f"離停損線 < {lim}%" if mkt == 'us' else
                         f"信用示警期間:離停損線 < 門檻 {lim}%"
                         f"(= 本檔 budget {(d.get('budget') or 0) * 100:.2f}% × 信用 {(health or 1):.0%} ÷ 0.5;"
                         f"只在 ≤ {lim_hi}% 的驗證範圍內檢查)"),
                'now': now, 'ok': far_ok,
                'verdict': '距離可接受' if far_ok else ('太遠 → 空手先別進(只影響新買進)' if mkt == 'us'
                                                       else '太遠 → 空手先別進(已持有的照信用乘數減碼)')})
    if mkt == 'us' and ext:
        r3 = _r3(d)
        out.append({'id': 'r3', 'name': '⑤ 小額試單(只在追高時檢查)',
                    'cond': '200MA上彎 且 VIX < 自己的20日均(恐慌退燒中)',
                    'now': '成立' if r3 else '不成立', 'ok': r3,
                    'verdict': '雖然追高,但此時「等拉回」沒有價值 → 可照系統曝險買進' if r3 else '維持等拉回'})
    vb = d.get('vbottom') or {}
    if vb.get('active'):
        out.append({'id': 'vbottom', 'name': '🚑 V底救援(系統空手時才檢查)',
                    'cond': '距252日高 <−15% 且 VIX 15日尖峰≥40 且 現值≤尖峰×75%',
                    'now': f"距高 {vb.get('dd252_pct')}% · VIX尖峰 {vb.get('vix_peak15')} → 現 {vb.get('vix_now')}",
                    'ok': True, 'verdict': '觸發 → 半倉進場,停損 −7%'})
    return out


def _load(name):
    with open(os.path.join(DASHBOARD_DIR, name), encoding='utf-8') as f:
        return json.load(f)


def _far_band(d, mkt, health):
    """台股「信用示警期間空手先別進」換算成價格:價格 ≤ buy_below 可買;> unchecked_above 不在驗證範圍、不檢查。
    只是把既有規則(taiwan_status.py)換算成價格,沒有新增規則。"""
    h = 1.0 if health is None else health
    stop, b = d.get('exit_price'), d.get('budget')
    if mkt != 'tw' or not (0 < h < 1) or not stop or not b:
        return None
    lim, lim_hi = b * h / 0.5, b / 0.5
    if lim >= 1 or lim_hi >= 1:
        return None
    return {'buy_below': round(stop / (1 - lim), 2), 'unchecked_above': round(stop / (1 - lim_hi), 2)}


def _r3(d):
    """R3(追高但趨勢上彎+恐慌退燒)→ 可照系統曝險買進。core_status 只把結果寫在 entry_action 文字裡。"""
    return '可小額試單' in str(d.get('entry_action', ''))


def _holder(d, mkt, health):
    """已持有的人今天該怎麼做。和回測一致:每天的曝險 = 目標曝險 × 信用健康比例(已持有的也照乘);
    跌破 200MA 但還在停損線之上(warning)= 續抱(回測的遲滯 intrend:≥MA 進、<MA×buf 出)。"""
    st = d.get('state')
    h = 1.0 if health is None else health
    cur = '$' if mkt == 'us' else 'NT$'
    stop = d.get('exit_price')
    if (d.get('vbottom') or {}).get('active'):   # 救援觸發時系統目標 = 半倉(core_status 已把 suggested_expo 設成 0.5)
        return 'reduce', f'🚑 V底救援:部位調到半倉,停損改為 {cur}{stop}(−7%)'
    if st == 'risk_off':
        return 'sell', f'收盤已跌破停損線 {cur}{stop} → 下一個交易日賣出'
    if d.get('credit_cut'):
        return 'sell', '信用哨全部跌破 → 下一個交易日全部賣出,等哨兵站回 200MA 再進'
    if st == 'panic_watch':
        return 'hold', '恐慌觀察:維持現倉、別加減,等 3 日確認(別砍在 V 底)'
    base = (f'跌破 200MA 但還在停損線 {cur}{stop} 之上 → 續抱,收盤跌破停損線才賣'
            if st == 'warning' else f'續抱,收盤跌破停損線 {cur}{stop} 才賣')
    if h < 1:
        return 'reduce', (f'信用部分示警 → 這一份只放 {h:.0%} 在股票裡(調到下方「目標部位」),其餘放現金;'
                          f'收盤跌破停損線 {cur}{stop} 才全部賣')
    return 'hold', base


def _entry(d):
    """空手的人今天該怎麼做。V底救援優先(它就是在系統空手/信用全砍時才觸發的進場訊號)。"""
    st, est = d.get('state'), d.get('entry_state')
    why = d.get('entry_action', '')
    if (d.get('vbottom') or {}).get('active'):
        return 'buy', why
    if d.get('credit_cut'):
        return 'cash', '信用哨全部跌破 → 不進場,等哨兵站回 200MA'
    if st in ('risk_off', 'panic_watch') or est == 'no_entry':
        return 'cash', why
    if est == 'can_enter':
        return 'buy', why
    if est == 'extended' and _r3(d):
        return 'buy', why
    if est in ('extended', 'expensive'):
        return 'wait', why
    return 'cash', why


def _positions(status, mkt, method, health=None):
    """回傳 [{ticker, name, weight, ...}]:weight = 該檔分到的資金比例(合計 ≤1,其餘現金)。"""
    cores = {tk: d for tk, d in (status.get('cores') or {}).items() if not d.get('watch_only')}
    if not cores:
        return []
    expected = EXPECTED_CORES[mkt]
    missing = [tk for tk in expected if tk not in cores]
    held = (status.get('rotation_hold') or {}).get('ticker') or status.get('strongest')
    if method == 'rotation':
        chosen = {held: 1.0} if held in cores else {}
    elif method == 'topn':
        n = TOPN[mkt]
        rank = sorted([tk for tk in cores if cores[tk].get('rs_score') is not None],
                      key=lambda tk: -cores[tk]['rs_score'])[:n]
        chosen = {tk: 1.0 / n for tk in rank}
    elif method == 'single':                # 只持有一檔寬基指數(research_b5/b6:與等權打平、交易少 4~6 倍)
        anchor = SINGLE_ANCHOR[mkt]
        chosen = {anchor: 1.0} if anchor in cores else {}
    elif method == 'ew3':                   # 台股去重:0050/006208 同一指數(相關0.896)只留 0050
        keep = [tk for tk in expected if tk != '006208.TW']
        chosen = {tk: 1.0 / len(keep) for tk in keep}
    else:                                   # 'ew':每檔固定 1/N 槽位(N = 應有的檔數,不是今天抓到的檔數)
        n = len(expected)
        chosen = {tk: 1.0 / n for tk in expected}
    out = []
    for tk, d in cores.items():
        w = chosen.get(tk, 0.0)
        st, est = d.get('state'), d.get('entry_state')
        hact, hwhy = _holder(d, mkt, health)
        act, why = _entry(d)
        # ★規則衝突標記(2026-09-23,Jason 指示「出現時標出來,當下自己決定」):
        #   價格掉到「停損線 ~ 200MA」之間時,單檔規則說「續抱」(還沒跌破停損線),
        #   但輪動的合格條件是「收盤 ≥ 200MA」→ 會判定失格、強制換掉。兩套規則對同一天給相反指示。
        #   回測顯示兩種版本差異 ≈ 0(±0.04),所以沒有「數據上正確」的一方,由人當下決定。
        #   只對「單押最強」有意義;等權/只持有一檔本身就是續抱到跌破停損線(前端只在 rotation 顯示)。
        conflict = (st == 'warning')
        out.append({
            'ticker': tk, 'name': d.get('name', ''), 'weight': w,   # 不四捨五入(1/3 存成 0.3333 會讓金額少 10 元)
            'conflict': conflict,
            'conflict_note': ('⚠️ 兩套規則不一致:價格在停損線與200MA之間——單檔規則說「還沒跌破停損線→續抱」,'
                              '輪動規則說「不在200MA之上→失格換掉」。回測上兩種做法差異≈0,你自己決定:'
                              '要嚴格(換掉)還是要少交易(續抱到真的跌破停損線)。') if conflict else '',
            'in_trend': st in ('risk_on', 'warning'), 'state': st, 'entry_state': est,
            'action': act, 'reason': why,                 # 空手的人
            'hold_action': hact, 'hold_reason': hwhy,     # 已持有的人
            'close': d.get('close'), 'limit': d.get('entry_cap'), 'stop': d.get('exit_price'),
            'limit_pct': d.get('entry_cap_pct'), 'stop_pct': d.get('exit_pct'),
            'expo': d.get('suggested_expo'), 'dist200_pct': d.get('dist_pct'),
            'dist50_pct': d.get('dist50_pct'), 'stop_risk_pct': d.get('stop_risk_pct'),
            'entry_thr_pct': round((d.get('entry_thr', 1) - 1) * 100, 1), 'rs': d.get('rs_score'),
            'budget': d.get('budget'), 'ma200': d.get('ma200'),
            'vbottom_active': bool((d.get('vbottom') or {}).get('active')),
            'far_band': _far_band(d, mkt, health),
            'trace': _trace(d, mkt, health),      # ★每條規則的檢查結果(條件/當下數值/門檻/依據)
        })
    for tk in missing:                      # 資料缺失的那一份:不給任何指示,叫人先別動
        out.append({'ticker': tk, 'name': '', 'weight': chosen.get(tk, 0.0), 'missing': True,
                    'conflict': False, 'conflict_note': '', 'state': None, 'entry_state': None,
                    'action': 'missing', 'reason': '今天沒抓到這一檔的資料 → 這一份先別動,等下一班更新',
                    'hold_action': 'missing', 'hold_reason': '今天沒抓到這一檔的資料 → 維持現狀,等下一班更新',
                    'close': None, 'limit': None, 'stop': None, 'expo': None, 'rs': None, 'trace': []})
    out.sort(key=lambda x: (-x['weight'], -(x['rs'] or 0)))
    return out


def _rules_version():
    """規則版本 = 規則相關檔案最後一次 commit 的日期(之前寫死 2026-09-23,改了規則也沒更新)。"""
    import subprocess
    try:
        out = subprocess.run(['git', 'log', '-1', '--format=%cs', '--', 'core_status.py', 'taiwan_status.py',
                              'v2_status.py', 'Web_Dashboard/v2_evidence.json'],
                             capture_output=True, text=True, timeout=10,
                             cwd=os.path.dirname(os.path.abspath(__file__))).stdout.strip()
        return out or None
    except Exception:
        return None


def build():
    cs, tw = _load('core_status.json'), _load('taiwan_status.json')
    data = {'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source': {'core_status': cs.get('last_updated'), 'taiwan_status': tw.get('last_updated')},
            'markets': {}}
    for mkt, status, cur in (('us', cs, '$'), ('tw', tw, 'NT$')):
        can = status.get('canary') or {}
        methods = {m: _positions(status, mkt, m, can.get('health')) for m in ('rotation', 'ew', 'ew3', 'single', 'topn')}
        data['markets'][mkt] = {
            'default_method': DEFAULT_METHOD[mkt], 'methods': methods, 'topn': TOPN[mkt], 'currency': cur,
            'credit_health': can.get('health'), 'credit_note': can.get('note'),
            'credit_assets': can.get('assets'),
            'sizing': ('RiskTarget:曝險 = min(budget ÷ 離停損距離, 150%) × 信用乘數'
                       if mkt == 'us' else '固定1倍 × 信用乘數(2026-09-23 改;RiskTarget 在台股5/5檔皆較差)'),
            'positions': methods[DEFAULT_METHOD[mkt]],
            'rotation_hold': status.get('rotation_hold'),
            'daily_verdict': status.get('daily_verdict'),
            'source_time': status.get('last_updated'),
            'price_date': status.get('price_date'),
            'credit_since': can.get('since'), 'credit_since_days': can.get('since_days'),
            'missing_cores': [tk for tk in EXPECTED_CORES[mkt]
                              if tk not in {k for k, d in (status.get('cores') or {}).items() if not d.get('watch_only')}],
            'missing_canary': status.get('missing_canary') or [],
            'intraday_tentative': bool(status.get('intraday_tentative')),
            'intraday_note': status.get('intraday_note') or '',
            'official': OFFICIAL[mkt],
        }
    # ★正2(00631L)衛星:live 只提供資料與規則,配多少由決策清單選(預設 0%)
    twc = (tw.get('cores') or {}).get(LEV_TICKER)
    if twc:
        # 閘門:母股 0050 站上 200MA 且正2 自己沒跌破停損線(research_b13_satellite_fix.py 的 live 版本就是這樣測的)
        g = (tw.get('cores') or {}).get('0050.TW') or {}
        gate_ok = g.get('state') in ('risk_on', 'warning') and not g.get('credit_cut')
        data['markets']['tw']['satellite'] = {
            'gate_0050_ok': bool(gate_ok), 'expo': twc.get('suggested_expo'),
            'ticker': LEV_TICKER, 'name': twc.get('name', ''), 'close': twc.get('close'),
            'limit': twc.get('entry_cap'), 'stop': twc.get('exit_price'),
            'limit_pct': twc.get('entry_cap_pct'), 'stop_pct': twc.get('exit_pct'),
            'state': twc.get('state'), 'entry_state': twc.get('entry_state'),
            'action': ('cash' if (not gate_ok or twc.get('state') == 'risk_off') else
                       'buy' if twc.get('entry_state') == 'can_enter' else 'wait'),
            'reason': twc.get('entry_action', ''),
            'dist200_pct': twc.get('dist_pct'),
            'gate_note': '持有條件:母股 0050 站上 200MA 且正2 收盤沒跌破自己的停損線,也乘信用乘數(回測就是這樣測的)。',
        }
    # ★共用哨兵一致性(2026-10-24 稽核 Q12:同一天 HYG 美股表 77.05、台股表 78.1,因為台股來源過期):
    #   兩支腳本各自下載,收盤差 >0.5% 就代表其中一份不是同一天的資料 → 標出來讓前端示警
    ua = data['markets']['us'].get('credit_assets') or {}
    ta = data['markets']['tw'].get('credit_assets') or {}
    mism = [tk for tk in ua if tk in ta and ua[tk].get('close') and ta[tk].get('close')
            and abs(ua[tk]['close'] / ta[tk]['close'] - 1) > 0.005]
    data['credit_mismatch'] = mism
    data['method_evidence'] = METHOD_EVIDENCE
    data['diff'] = DIFF
    data['limits'] = LIMITS
    data['decisions'] = [dict(d, verdict=DECISION_VERDICT.get(d['key'], '')) for d in DECISIONS]
    data['findings'] = FINDINGS
    data['research_todo'] = RESEARCH_TODO
    data['research_pending'] = RESEARCH_PENDING
    data['rules_version'] = _rules_version()
    data['rule_evidence'] = {k: {'rule': v[0], 'evidence': v[1], 'plain': v[2], 'grade': v[3]}
                             for k, v in RULE_EVIDENCE.items()}
    path = os.path.join(DASHBOARD_DIR, 'v2_status.json')
    with open(path, 'w', encoding='utf-8') as f:
        from scanner_base import json_safe
        json.dump(json_safe(data), f, ensure_ascii=False, indent=2, allow_nan=False)
    print(f'💾 {path}')
    for mkt in ('us', 'tw'):
        m = data['markets'][mkt]
        print(f"\n[{mkt}] 預設持有方式={m['default_method']} 信用健康={m['credit_health']}")
        for p in m['positions']:
            print(f"   {p['ticker']:10s} 權重{p['weight']*100:5.1f}% {p['action']:9s} "
                  f"現價{p['close']} 限價{p['limit']} 停損{p['stop']} 曝險{p['expo']}")
    return data


if __name__ == '__main__':
    build()
