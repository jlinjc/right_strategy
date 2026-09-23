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
METHOD_EVIDENCE = {
    'us': {'default': 'rotation', 'verdict': '打平 — 沒有證據更換',
           'detail': '主判定池(SMH/SOXX/QQQ/XLK/SPY,2002-2026):等權 − 單押最強 = 超額Sharpe +0.013、p=0.41、'
                     '同波動年化 +0.03pp → 兩者沒有差別;前 N 名曲線平坦(1檔0.71/2檔0.67/3檔0.69/4檔0.68/5檔0.68)'
                     '→「挑選訊號無法判定」。但無後見之明的 14 檔產業池:等權 +0.264、p=0.024、三段全正、'
                     '同波動年化 +4.3pp,明顯站在等權那邊;「挑選 alpha」t 值僅 1.66(不顯著)。'
                     '回撤:單押 −36.1% vs 等權 −26.0%。',
           'options': {'rotation': '單押 RS 最強一檔(現行,持滿30日才准換);換手少(約15筆/年)',
                       'ew': '5 檔各 1/5,各自看自己的 200MA;回撤較淺,但換手多(約70筆/年)'}},
    'tw': {'default': 'ew', 'verdict': '等權勝出 — 已改',
           'detail': '主判定池(0050/0052/006208/0051,2013-2026):等權 − 單押最強 = 超額Sharpe +0.210、p=0.038、'
                     '同波動年化 +4.28pp、三段有兩段為正,2倍成本/統一緩衝/RiskTarget倉位/21個換倉日全部同方向;'
                     '長樣本池(0050/0052/0051,2009起)+0.229、p=0.015(Holm 校正後 0.045 過關)。'
                     '前 N 名曲線單調上升:1檔1.06 → 2檔1.16 → 3檔1.23 → 4檔1.22。'
                     '同群換倉(0050↔006208)占 25%,換完之後 21 日平均 −0.19% = 白付成本。'
                     '⚠️主池顯著性 Holm 校正後 0.114,差一點沒過 → 證據等級「中等」。',
           'options': {'ew': '4~5 檔各占一份,各自看自己的 200MA(新版預設)',
                       'rotation': '單押 RS 最強一檔(原版做法)'}},
}


LEV_TICKER = '00631L.TW'

# ── 決策清單:每條都給正反兩邊的實測數字,選了就直接影響上面的「今天怎麼做」──
#    數字來源:research_q4_choices.py(A 槓桿階梯 / D 正2接法)、research_rot_verify_p1.py(持有方式)
DECISIONS = [
    {'key': 'method_us', 'title': '美股要單押最強,還是 5 檔等權?(你已選:等權)',
     'note': '主判定池 2002-2026 統計上打平(Δ超額Sharpe +0.013、p=0.41);無後見之明的 14 檔產業池則明顯站等權。',
     'options': [
         {'v': 'rotation', 'label': '單押最強(現行)', 'nums': 'CAGR 14.1% · 回撤 −36.1% · 約15筆/年 · 超額Sharpe 0.69'},
         {'v': 'ew', 'label': '5 檔等權', 'nums': 'CAGR 12.4% · 回撤 −26.0% · 約70筆/年 · 超額Sharpe 0.70(同波動後年化一樣)'}]},
    {'key': 'method_tw', 'title': '台股要等權,還是單押最強?',
     'note': '主池 Δ+0.210(p=0.038)、長樣本池 Δ+0.229(p=0.015);前N名曲線 1檔1.06→3檔1.23。',
     'options': [
         {'v': 'ew', 'label': '等權(新版預設)', 'nums': 'CAGR 19.3% · 回撤 −20.3% · 超額Sharpe 1.26'},
         {'v': 'rotation', 'label': '單押最強(原版)', 'nums': 'CAGR 13.2% · 回撤 −33.7% · 超額Sharpe 0.89'}]},
    {'key': 'lev_us', 'title': '美股要開多少槓桿?(頁面同時列出 ×1.0 與 ×1.25 的金額)',
     'note': '2002-2026 單押輪動+RiskTarget。Sharpe 幾乎不隨槓桿變(0.69~0.73)=沿同一條效率線滑動,'
             '差別只有「賺多少 vs 跌多深」。$10萬起算的期末值供感受規模。',
     'options': [
         {'v': '0.5', 'label': '×0.5(保守)', 'nums': 'CAGR 10.5% · 回撤 −27.0% · 10萬→114萬'},
         {'v': '0.75', 'label': '×0.75', 'nums': 'CAGR 14.3% · 回撤 −38.6% · 10萬→260萬'},
         {'v': '1', 'label': '×1.0(現行)', 'nums': 'CAGR 17.7% · 回撤 −48.6% · 10萬→529萬'},
         {'v': '1.25', 'label': '×1.25(積極)', 'nums': 'CAGR 20.9% · 回撤 −57.2% · 10萬→1017萬'}]},
    {'key': 'lev_tw', 'title': '台股要開多少槓桿?(頁面同時列出 ×1.0 與 ×1.25 的金額)',
     'note': '2013-2026 等權+固定1倍。同樣是沿效率線滑動(Sharpe 1.21~1.26)。台股融資成本較高,'
             '×1.25 以上要自己確認券商利率。',
     'options': [
         {'v': '0.5', 'label': '×0.5(保守)', 'nums': 'CAGR 10.2% · 回撤 −11.3%'},
         {'v': '0.75', 'label': '×0.75', 'nums': 'CAGR 15.1% · 回撤 −16.6%'},
         {'v': '1', 'label': '×1.0(現行)', 'nums': 'CAGR 19.3% · 回撤 −20.3%'},
         {'v': '1.25', 'label': '×1.25(積極)', 'nums': 'CAGR 23.3% · 回撤 −24.4%'}]},
    {'key': 'sat_tw', 'title': '台股要不要配正2(00631L)當衛星?',
     'note': '2015-08~2026-09 實測:加正2衛星比純核心差;想加報酬,直接把核心加槓桿比較划算。'
             '⚠️2015 年正2擇時單年 −41%、純買持最差年 −36%。這條是你指定要接的,數字照實列。',
     'options': [
         {'v': '0', 'label': '不配(數據最佳)', 'nums': '核心等權:CAGR 21.8% · 回撤 −20.3% · Sharpe 1.36'},
         {'v': '0.2', 'label': '衛星 20%', 'nums': 'CAGR 22.0% · 回撤 −22.0% · Sharpe 1.28(正2用0050的200MA當閘門)'},
         {'v': '0.4', 'label': '衛星 40%', 'nums': 'CAGR 22.5% · 回撤 −25.5% · Sharpe 1.10 · 最差年 −18.7%'},
         {'v': '1', 'label': '全押正2+擇時(最兇)', 'nums': 'CAGR 28.5% · 回撤 −47.2% · Sharpe 0.92 · 最差年 −41.2%'}]},
    {'key': 'pool_tw', 'title': '台股要用 4 檔還是 3 檔?(0050 和 006208 追蹤同一個指數)',
     'note': '0050 與 006208 日報酬相關 0.896(同一個台灣50指數,只是發行商不同)。等權下兩檔合計占 50%。'
             '實測 2013-2026(等權+信用哨):4檔 CAGR 22.2%/Sharpe 1.62/回撤 −17.7%;'
             '去掉 006208 的 3 檔 22.0%/1.60/−17.6% → ΔSharpe −0.021(p=0.861)= 幾乎完全一樣,'
             '但交易從 100 筆/年降到 75 筆/年。',
     'options': [
         {'v': '4', 'label': '4 檔(0050+0052+006208+0051)', 'nums': 'CAGR 22.2% · Sharpe 1.62 · 約100筆/年'},
         {'v': '3', 'label': '3 檔(去掉 006208,少一半重複)', 'nums': 'CAGR 22.0% · Sharpe 1.60 · 約75筆/年(省25筆)'}]},
    {'key': 'credit_us', 'title': '美股信用哨要用哪一版?(台股那版已驗證有效,不動)',
     'note': '2026-09-24 重驗:美股信用哨整體效果很小(ΔSharpe +0.08、p=0.17,校正後不顯著)。'
             '兩個版本風險幾乎一樣,差在報酬與交易量。',
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
     'why': '台股 +0.210(p=0.038)/長樣本 +0.229(p=0.015);美股打平,Jason 選回撤較淺的等權(rot_verify)'},
    {'item': '台股標的', 'old': '0050/0052/006208/0051/00757', 'new': '0050/0052/006208/0051(移除 00757)',
     'why': '00757=美股FANG+,與既有美股部位重複;9個候選沒有一檔達標(b3_tw_replace)'},
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
    {'date': '2026-09-24', 'title': '台股信用哨是真 edge,美股的幾乎沒用',
     'body': '等權基礎重驗:台股 ΔSharpe +0.359(p<0.001,多重檢定後仍成立),2015/2018/2022 三個壞年份'
             '從 −4.9/−4.9/−9.8% 翻成 +1.4/+2.8/+2.3%;美股只有 +0.080(p=0.172)。',
     'status': '已落地:台股維持三合哨;美股保留但標示為弱', 'script': 'research_b1_credit_canary.py'},
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
    {'date': '2026-09-23', 'title': '新版 vs 原版(台股):年化 13.2% → 19.3%、回撤 −33.7% → −20.3%',
     'body': '等權+固定1倍 vs 單押輪動+RiskTarget:ΔSharpe +0.375(p=0.041);長樣本池 +0.391(p=0.011)。美股兩版相同。',
     'status': '已落地', 'script': 'research_q4_choices.py'},
    {'date': '2026-09-23', 'title': 'RS「挑最強」沒有可證實的選股能力',
     'body': '四個池子的挑選 alpha t 值 1.66/0.29/0.59/0.11 全 <2;12 個候選挑選機制校正後全部沒過。'
             '分散(等權)才是穩定的來源。',
     'status': '已落地:兩市場都改等權', 'script': 'research_rot_verify_p1.py / p2.py'},
]
RESEARCH_TODO = [
    {'item': '個股衛星整套重驗(S&P500 掃描、進出場、倉位)', 'why': 'live 在用,這一輪完全沒碰', 'status': '待辦'},
    {'item': '進取模式(TQQQ/SOXL)', 'why': '用獨立的二元信用哨,規則和核心不一致', 'status': '待辦'},
    {'item': '美股「池子」本身', 'why': '等權在無後見之明 14 檔池大勝(+0.264),在現用 5 檔池打平 → 換更分散的池可能比怎麼分配更重要', 'status': '待辦'},
    {'item': '事前選池規則(流動性/費用率/類別)', 'why': '現用 5 檔是事後挑的(4368 種組合中排 147)', 'status': '待辦'},
    {'item': '等權的實務摩擦', 'why': '月 vs 季再平衡、台股零股與手續費對小資金的影響,決定實際能不能照做', 'status': '待辦'},
    {'item': 'XLK 的信用門檻 credit_k=0.98', 'why': '個別標的門檻未單獨驗證', 'status': '待辦'},
    {'item': '未跑完的驗證關卡', 'why': 'Deflated Sharpe、檢查池候選層、bootstrap/種子數降過規格', 'status': '待辦'},
    {'item': '前瞻紙上追蹤 12 個月', 'why': '2002-2026 研究時已全部看過,沒有真正樣本外;從 2026-10 起並排追蹤', 'status': '待開始'},
    {'item': '信用哨重驗', 'why': '—', 'status': '✅ 2026-09-24 完成'},
    {'item': 'vol-timing / 恐慌容忍 / V底救援重驗', 'why': '—', 'status': '✅ 2026-09-24 完成'},
    {'item': '所有結論補算 p 值', 'why': '—', 'status': '✅ 2026-09-24 完成'},
    {'item': '00757 替代標的', 'why': '—', 'status': '✅ 2026-09-24 完成(不需替代)'},
]

LIMITS = [
    '訊號全部用<b>收盤價</b>,盤中數字只是暫定;22:30 那班是美股盤中,決策看隔天早上那班。',
    '回測期間:美股 2002-2026、台股 2013-2026(0050/0052/0051 可回到 2009)。台股樣本短,結論強度本來就低於美股。',
    '2002-2026 的資料在研究過程中已經全部看過,沒有真正的樣本外。任何新做法都應該先紙上追蹤 12 個月。',
    '信用哨(HYG/LQD/SOXX)沿用既有研究,這一輪<b>沒有</b>重新驗證。',
    '成本假設:美股 0.05%、台股 0.10%/單邊(已含賣出證交稅的粗估)。所有判定都在 2 倍成本下重跑過,方向不變。',
    '槓桿、每月定投、個股衛星等其他面板不在這一版範圍內,請回原版看。',
]


# ── 規則依據(每條規則的門檻怎麼來、有沒有回測、效果多大)──
#    給前端逐條顯示,不准只寫結論不寫數字。
RULE_EVIDENCE = {
    'trend': ('收盤 ≥ 200日均線才持有;收盤跌破 200MA×緩衝 全部轉現金',
              '緩衝各檔不同(美股0.98~1.00、台股0.98~1.00),來自舊研究的逐檔校準。'
              '擇時本身的價值:SPY 回撤 −55%→−21%、SMH −68%→−31%、0050 −34%→−21%(research_q1)。'),
    'credit': ('信用哨健康比例 = 曝險乘數',
               '2026-09-24 重驗(research_b1_credit_canary.py,等權基礎):'
               '台股 ΔSharpe +0.359、p<0.001、Holm 校正後仍過、三段全正,回撤 −20.3→−17.7%;'
               '美股 +0.080、p=0.172(校正後 0.69)=無效但無害。'),
    'extended_us': ('離 50 日均線 ≥ 門檻 → 別追高(等拉回)',
                    '門檻來自各檔逐檔校準(SMH/XLK +10%、SOXX/QQQ +7%、SPY +5%)。'
                    '2026-09-23 到場測試驗證:等拉回較好 67%、32年中27年、平均 +0.43%;'
                    '但 2026-09-24 用更保守的逐年 bootstrap 重算 p=0.130(不顯著)→ 方向對、強度弱。'),
    'extended_tw': ('離 50 日均線 ≥ +12% → 別追高(等拉回)',
                    '2026-09-23 掃 3%~15% 一整排門檻後選出的平台區中間值:+12% 等待較好 71%、平均 +1.08%,'
                    '前半(<2018)4年中3年、後半 6/6 年、5檔全正;+10% 不成立。'
                    'p<0.001,是所有結論中唯一撐過整體多重檢定的一條。舊門檻 +6~8% 反而有害(p=0.955)。'),
    'far_us': ('沒追高但離停損線 ≥ 20% → 空手先別進',
               '舊版用「曝險<50%」= 離停損 38~43%,美股 25 年一天都沒觸發過(等於沒這條)。'
               '改用距離掃描:只看沒被別追高擋下的日子,離停損 ≥20% 時等拉近再買平均 +0.66%、14年中12年較好;'
               '但逐年 bootstrap p=0.200(不顯著)→ 保留是因為方向一致且無害。'),
    'far_tw': ('信用示警期間,離停損線 > budget×信用健康度÷0.5 → 空手先別進',
               '台股平時版本被反證(離停損 12~18% 時等待平均少賺 3~4%、勝率<40%)已移除;'
               '只有信用示警期間的版本有支撐:等待平均 +0.63%、勝64%、14年中11年,前半6/6、後半5/8。'),
    'r3': ('別追高的日子裡,若 200MA 上彎 且 VIX 低於自己20日均(恐慌退燒)→ 可小額試單',
           '「小額試單」= 不等拉回、先用系統算出的曝險買進(不是額外加碼)。'
           '依據:R3 成立時「等拉回」和「立即買」幾乎沒差(平均 −0.19%),所以等待沒有價值;'
           'R3 不成立時等拉回較好(+0.41%)。台股同現象但逐年不一致 → 台股沒有這條。'),
    'vbottom': ('系統空手 + 距252日高回撤>15% + VIX從≥40尖峰退燒至75% → 半倉救援進場,停損 −7%',
                '台股 2026-09-24 新增:等權基礎 ΔSharpe +0.137、p=0.014、Holm 後 0.056、三段全正,'
                'CAGR 19.3→22.1%(research_b2_overlays.py)。美股同測僅 +0.025,維持 SMH/SOXX 限定。'),
    'sizing_us': ('曝險 = min(budget ÷ 離停損距離, 150%) × 信用乘數',
                  '實測 ≈ 同平均槓桿的固定倍數(ΔSharpe +0.01~0.03)→ 多賺的是槓桿本身。'
                  'vol-timing 已於 2026-09-24 關閉(+0.008、p=0.374,但交易 70→133 筆/年)。'),
    'sizing_tw': ('曝險 = 100% × 信用乘數(固定1倍)',
                  'RiskTarget 在台股逐檔 5/5、2018後 5/5 都較差(組合層 p=0.367),2026-09-23 改固定1倍。'),
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
    out.append({'id': 'trend', 'name': '① 趨勢:站上200MA?',
                'cond': f"收盤 ≥ 200MA(={ma})", 'now': f"收盤 {close}(離200MA {dist:+.1f}%)" if dist is not None else '—',
                'ok': ok_trend, 'verdict': '在趨勢中' if ok_trend else '跌破 → 這份放現金'})
    out.append({'id': 'credit', 'name': '② 信用哨:今天的曝險上限',
                'cond': '健康的哨兵數 ÷ 全部哨兵 = 曝險乘數',
                'now': f"健康比例 {health:.0%} → 每筆量 ×{health:.0%}" if health is not None else '—',
                'ok': (health or 0) > 0, 'verdict': ('全額' if (health or 0) >= 1 else
                                                     ('減量' if (health or 0) > 0 else '清倉不買'))})
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
        far_ok = not (0 < (health or 1) < 1 and sr is not None and sr > lim)
        now = f"離停損線 {sr}%" + ('' if 0 < (health or 1) < 1 else '(信用滿血時這條不啟用)')
    out.append({'id': 'far_' + mkt, 'name': '④ 空手先別進:離停損線太遠嗎?',
                'cond': (f"離停損線 < {lim}%" if mkt == 'us' else f"信用示警期間:離停損線 < {lim}%"),
                'now': now, 'ok': far_ok,
                'verdict': '距離可接受' if far_ok else '太遠 → 空手先別進(已持有者續抱)'})
    if mkt == 'us' and ext:
        r3 = '可小額試單' in str(d.get('entry_action', ''))
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


def _positions(status, mkt, method, health=None):
    """回傳 [{ticker, name, weight, ...}]:weight = 該檔分到的資金比例(合計 ≤1,其餘現金)。"""
    cores = {tk: d for tk, d in (status.get('cores') or {}).items() if not d.get('watch_only')}
    if not cores:
        return []
    held = (status.get('rotation_hold') or {}).get('ticker') or status.get('strongest')
    if method == 'rotation':
        chosen = {held: 1.0} if held in cores else {}
    elif method == 'topn':
        n = TOPN[mkt]
        rank = sorted([tk for tk in cores if cores[tk].get('rs_score') is not None],
                      key=lambda tk: -cores[tk]['rs_score'])[:n]
        chosen = {tk: 1.0 / n for tk in rank}
    else:                                   # 'ew':每檔固定 1/N 槽位
        n = len(cores)
        chosen = {tk: 1.0 / n for tk in cores}
    out = []
    for tk, d in cores.items():
        w = chosen.get(tk, 0.0)
        st, est = d.get('state'), d.get('entry_state')
        if d.get('credit_cut') or st == 'risk_off':
            act, why = 'cash', '跌破200MA或信用全示警 → 這份放現金'
        elif st == 'panic_watch':
            act, why = 'hold_wait', '恐慌觀察:維持現倉,等3日確認(別砍V底)'
        elif est == 'can_enter':
            act, why = 'buy', d.get('entry_action', '')
        elif est in ('extended', 'expensive'):
            act, why = 'wait', d.get('entry_action', '')
        else:
            act, why = 'cash', d.get('entry_action', '')
        # ★規則衝突標記(2026-09-23,Jason 指示「出現時標出來,當下自己決定」):
        #   價格掉到「停損線 ~ 200MA」之間時,單檔規則說「續抱」(還沒跌破停損線),
        #   但輪動的合格條件是「收盤 ≥ 200MA」→ 會判定失格、強制換掉。兩套規則對同一天給相反指示。
        #   回測顯示兩種版本差異 ≈ 0(±0.04),所以沒有「數據上正確」的一方,由人當下決定。
        conflict = (st == 'warning')
        out.append({
            'ticker': tk, 'name': d.get('name', ''), 'weight': round(w, 4),
            'conflict': conflict,
            'conflict_note': ('⚠️ 兩套規則不一致:價格在停損線與200MA之間——單檔規則說「還沒跌破停損線→續抱」,'
                              '輪動規則說「不在200MA之上→失格換掉」。回測上兩種做法差異≈0,你自己決定:'
                              '要嚴格(換掉)還是要少交易(續抱到真的跌破停損線)。') if conflict else '',
            'in_trend': st in ('risk_on', 'warning'), 'state': st, 'entry_state': est,
            'action': act, 'reason': why,
            'close': d.get('close'), 'limit': d.get('entry_cap'), 'stop': d.get('exit_price'),
            'limit_pct': d.get('entry_cap_pct'), 'stop_pct': d.get('exit_pct'),
            'expo': d.get('suggested_expo'), 'dist200_pct': d.get('dist_pct'),
            'dist50_pct': d.get('dist50_pct'), 'stop_risk_pct': d.get('stop_risk_pct'),
            'entry_thr_pct': round((d.get('entry_thr', 1) - 1) * 100, 1), 'rs': d.get('rs_score'),
            'budget': d.get('budget'), 'ma200': d.get('ma200'),
            'trace': _trace(d, mkt, health),      # ★每條規則的檢查結果(條件/當下數值/門檻/依據)
        })
    out.sort(key=lambda x: (-x['weight'], -(x['rs'] or 0)))
    return out


def build():
    cs, tw = _load('core_status.json'), _load('taiwan_status.json')
    data = {'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source': {'core_status': cs.get('last_updated'), 'taiwan_status': tw.get('last_updated')},
            'markets': {}}
    for mkt, status, cur in (('us', cs, '$'), ('tw', tw, 'NT$')):
        can = status.get('canary') or {}
        methods = {m: _positions(status, mkt, m, can.get('health')) for m in ('rotation', 'ew', 'topn')}
        data['markets'][mkt] = {
            'default_method': DEFAULT_METHOD[mkt], 'methods': methods, 'topn': TOPN[mkt], 'currency': cur,
            'credit_health': can.get('health'), 'credit_note': can.get('note'),
            'credit_assets': can.get('assets'),
            'sizing': ('RiskTarget:曝險 = min(budget ÷ 離停損距離, 150%) × 信用乘數'
                       if mkt == 'us' else '固定1倍 × 信用乘數(2026-09-23 改;RiskTarget 在台股5/5檔皆較差)'),
            'positions': methods[DEFAULT_METHOD[mkt]],
            'rotation_hold': status.get('rotation_hold'),
            'daily_verdict': status.get('daily_verdict'),
        }
    # ★正2(00631L)衛星:live 只提供資料與規則,配多少由決策清單選(預設 0%)
    twc = (tw.get('cores') or {}).get(LEV_TICKER)
    if twc:
        # 閘門:回測中最好的接法是用母股 0050 的 200MA 當閘門(③ Sharpe 1.28 > ② 自己的 1.21)
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
            'gate_note': '正2的停損線用它自己的200MA;母股 0050 跌破自己的200MA時,核心那幾份本來就會轉現金。',
        }
    data['method_evidence'] = METHOD_EVIDENCE
    data['diff'] = DIFF
    data['limits'] = LIMITS
    data['decisions'] = DECISIONS
    data['findings'] = FINDINGS
    data['research_todo'] = RESEARCH_TODO
    data['rule_evidence'] = {k: {'rule': v[0], 'evidence': v[1]} for k, v in RULE_EVIDENCE.items()}
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
