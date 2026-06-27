# Right Strategy — 右側動能拉回交易系統

> ⚠️ 本專案為個人研究／實盤輔助工具，所有回測結果僅供參考，不構成投資建議。
> 帳面 Sharpe 建議打 7–8 折看待，正式投入前請先 paper trading ≥ 3 個月。

---

## 📌 這個 repo 包含「兩套獨立系統」

這個專案是從一個**功能龐雜的舊儀表板**，演進到一套**用數據逐項驗證、聚焦單一策略的新交易系統**。
兩者**完全獨立、互不依賴**，請先看懂下面這張對照表，再決定要看／要跑哪一套：

| | 🟢 **新系統（本 README 主角）** | ⚪ 舊系統（保留，僅供參考） |
|---|---|---|
| 名稱 | **右側動能拉回策略** | 多功能美股儀表板 |
| 定位 | **一套明確、有紀律、可重複的交易策略** | 一堆掃描器 + 分析面板的集合 |
| 進場頁面 | `strategy_dashboard.html`（單頁作戰中心） | `index.html`（11 分頁儀表板） |
| 啟動指令 | **`python start_strategy.py`** | `python start_dashboard.py` |
| 哲學 | 少即是多，每條規則都經回測驗證 | 功能多，多數未經嚴格回測 |
| 狀態 | **持續維護、實盤主力** | 凍結保留、非主力 |

> 👉 **想交易的話，只要看下面的「PART 1」、跑 `start_strategy.py` 就好。**
> 舊系統的內容在最後面「PART 2」簡單帶過。

---

# PART 1 — 🟢 右側動能拉回策略（主系統）

一套以**數據驅動、逐步消除假設**方式定案的美股波段交易策略。核心思路來自
Minervini SEPA / O'Neil CANSLIM 的「右側動能 + 均線拉回」，並用 2～3 年的
walk-forward 回測、敏感度分析、Monte Carlo 風險模擬與生存者偏差檢驗逐項驗證。

**核心信念：在動能交易裡，「交易什麼」遠比「怎麼進出」重要。**
因此本策略最關鍵的一步不是進場訊號，而是**用規則化的相對強度（RS）排名，系統性地只交易市場領導股**。

## 1. 策略總覽（最終定案版，2026-06）

### 1.1 股池選擇 — 用規則取代「手挑贏家」
- 廣股池（**109 檔**，橫跨 AI／半導體／核能／傳產／金融／能源／防禦性等全板塊，去重）
- 每日計算**風險調整動能 RS（voladj = 6 個月報酬 ÷ 波動率）**，跨全股池排百分位
- 只允許交易 **RS 排名前 20%（≥ 80 分）** 的個股
- 用規則重現「交易領導股」，避免回測使用「事後贏家股池」造成的生存者偏差
- 為何用 voladj？它偏好**平滑、持續**的強勢股，自動避開最會崩的飆股 → 降低動能崩盤（momentum crash）風險

### 1.2 進場條件（四項，全部滿足才進場）
1. **RS 排名 ≥ 80**（股池前 20% 最強股）— 最重要的一關
2. **均線拉回**：強勢股拉回觸及均線（Minervini 拉回買點）並收在均線之上
3. **TTM 動能 > 0**（核心必備濾網，跨多空環境最穩定的真實 edge）
4. **不追高**：股價 < 1.08 × 10 日均線（避免買在噴出末端）

> **基本面（淨利率、營收成長、ROE、前瞻 PE）不做硬性篩選。**
> 經回測證實 RS 動能已隱含吸收基本面資訊，硬篩反而誤殺「貴但最強」的領導股。
> 改為在**信號卡上「顯示」**這些數字，供你裁量減碼（例：看到負利潤的故事股就降部位）。

### 1.3 出場規則 — 分批出場（研究代號 Ⓗ）
1. 股價漲到 **進場價 + 3×ATR** 時，賣出 **50%** 倉位（落袋為安、提高勝率）
2. 剩餘 50% 用 **3.5×ATR 吊燈停損（Chandelier Exit）** 移動出場，抱住趨勢肥尾
3. 任何時候跌破**初始硬停損**立即出清
4. **最長持有 40 天**（時間停損，防止資金卡死在橫盤股）

### 1.4 風險控管
- 單筆風險 = 帳戶 **1%**
- 組合總風險上限（Portfolio Heat）= **3%**（壓低相關標的同時曝險，是真實回撤的關鍵閥門）
- 單股持倉上限 = 帳戶 **20%**　｜　同板塊持倉上限 = **3 檔**
- **系統化大盤 regime gate（QQQ>200MA 等）經測試後否決不採用**——
  個股層的 RS＋動能＋均線濾網已內建市場環境判斷，疊加指數濾網反而拖累 Sharpe。
  大盤燈號僅作為「裁量式風險旋鈕」（轉空時手動減碼），不寫成硬規則。

### 1.5 誠實期望值（3 年 walk-forward，含修正期，已扣除生存者偏差）
| 指標 | 數值 |
|------|------|
| Sharpe | **~1.9**（換用 voladj RS 後） |
| Profit Factor | ~2.7 |
| 勝率 | ~43% |
| 回測最大回撤 | -11% |
| **規劃承受底線**（含跳空／相關性風險） | **-25%** |

- 統計顯著性：360 筆交易，**t = 5.95（p ≈ 0）**，已過約 36 種組態的多重測試門檻 → edge 是真實的，不是挑出來的雜訊。
- 誠實提醒：右側動能策略**靠賠率不靠勝率**（勝率僅 ~43%，但贏家平均是輸家的 ~2.7 倍）；且**停損擋不住跳空**，唯靠「小部位＋分散」管理尾部風險。

## 2. 系統架構（新系統檔案）

```
┌──────────────────┐   ┌───────────────────┐   ┌─────────────────────────┐
│ 回測 / 驗證工具群    │   │ 即時訊號產生         │   │ Web 作戰中心              │
│ backtest_engine   │──▶│ generate_signals   │──▶│ strategy_dashboard.html  │
│ validate_*        │   │ rs_selection       │   │ web_server.py            │
│ compare_*         │   │ generate_evidence  │   │ start_strategy.py        │
└──────────────────┘   └───────────────────┘   └─────────────────────────┘
```

### 2.1 核心引擎與策略
| 檔案 | 說明 |
|------|------|
| `backtest_engine.py` | 事件驅動回測引擎：Portfolio、RiskManager（含 portfolio heat 閘門）、`scale_out()` 分批出場 |
| `backtest_strategies.py` | 基礎策略：`MAPullbackStrategy`（主策略）等 |
| `exit_experiments.py` | 各種出場規則（基準線／吊燈／Fib／亞當鏡像／分批混合 Ⓗ） |
| `filter_experiments.py` | 進場濾網（TTM 動能、不追高、量能、ADX、RS margin、大盤 regime） |
| `rs_selection.py` | **橫斷面 RS 排名（voladj 模式）+ `RSRankScaledExit` 完整策略** |
| `fundamentals_filter.py` | 基本面濾網工廠（研究用）＋當下基本面抓取快取 |

### 2.2 即時訊號與 Web
| 檔案 | 說明 |
|------|------|
| `generate_signals.py` | 用定案策略即時掃描廣股池 → 今日進場信號卡 + 大盤 regime + 觀察清單 → `strategy_signals.json` |
| `generate_evidence.py` | 匯出完整回測研究歷程 → `backtest_evidence.json`（dashboard 實證面板） |
| `validate_robustness.py` | 統計顯著性（t 檢定）+ Monte Carlo 回撤分布 → `robustness.json` |
| `Web_Dashboard/strategy_dashboard.html` | **單頁作戰中心**：regime 燈號 / 策略紀律總覽 / 今日信號卡 / 真實風險 / 觀察清單 / 回測實證 |
| `web_server.py` | 本地伺服器（靜態檔 + API） |
| `start_strategy.py` | **一鍵啟動**：跑訊號 + 實證 → 開站 → 自動開作戰中心 |

### 2.3 驗證 / 實驗工具（研究歷程，每個結論都可重跑驗證）
| 檔案 | 用途 / 結論 |
|------|------|
| `validate_strategy.py` | 官方 walk-forward 驗證（PASS/CONDITIONAL/FAIL + 敏感度網格） |
| `validate_universe.py` | 生存者偏差檢驗（中性股池 Sharpe≈0 → edge 來自股池選擇） |
| `compare_strategies.py` | 多策略比較（確認 MA 拉回 = 全部 edge，TD9／動能突破冗餘） |
| `compare_exits.py` | 出場規則比較（分批出場 Ⓗ 勝出） |
| `compare_filters.py` | 進場濾網比較（動能＋不追高有效；放量／RS 否決） |
| `compare_regime.py` | 系統化大盤 regime gate（全部否決） |
| `compare_concentration.py` | 集中度／組合熱度（heat 上限 3% 有效降回撤） |
| `compare_rs.py` | 橫斷面 RS 選股（救回 edge 至 ~1.5） |
| `compare_rs_metric.py` | RS 指標定義比較（**voladj 風險調整動能勝出 → ~1.9**） |
| `compare_filters_fund.py` | 基本面濾網污染研究（結論：不做硬篩） |

## 3. 快速開始

```bash
pip install pandas numpy yfinance lxml   # lxml 供財報排程(PEAD/盲區)抓取

# ── 一鍵啟動作戰中心（主要用法）──
python start_strategy.py
# → 自動開啟 http://localhost:8000/strategy_dashboard.html

python start_strategy.py --data-only   # 只更新訊號資料，不開網頁

# ── 重跑驗證 ──
python validate_strategy.py --fast     # 2 年快速版
python validate_robustness.py          # 統計顯著性 + Monte Carlo 回撤
python compare_rs_metric.py            # RS 指標比較
```

## 4. 策略演進歷程（為什麼敢照這套做）

完整記錄於 `backtest_evidence.json`，並顯示於作戰中心「回測實證」面板。關鍵里程碑：

1. **三策略混合 → 單一 MA 拉回**：拆開測試，TD9／動能突破單獨皆為負 Sharpe，混入反而拖累。
2. **出場規則比較**：分批出場（Ⓗ：+3×ATR 賣 50% + 剩餘吊燈 3.5×ATR）取得 Sharpe／勝率／PF／MDD 最佳平衡。
3. **進場濾網**：TTM 動能為跨環境最穩 edge；不追高加分；放量反彈災難級負貢獻，否決。
4. **系統化 regime gate**：全數否決，個股層濾網已隱含市場判斷。
5. **生存者偏差檢驗**：手挑 AI 股池在中性股池上 Sharpe ≈ 0 → 原始高分主要來自「股池選擇」。
6. **橫斷面 RS 排名選股**：用規則重現「交易領導股」，把系統化版本 Sharpe 從 ~1.0 救回 ~1.5。
7. **RS 指標優化**：風險調整動能（voladj）四項指標全面最佳，拉到 ~1.9，已設為預設。
8. **統計穩健性**：t = 5.95 高度顯著；Monte Carlo 顯示相關性曝險是真實回撤主因 → 組合熱度上限（3%）改善 MDD。

## 5. 待辦事項

- [ ] 交易日誌寫入（目前持倉讀 `Web_Dashboard/positions.json`，尚無自動下單／日誌）
- [ ] 2021–2022 科技殺盤段的 momentum crash 壓力測試（最該補的「壞天氣」驗證）
- [ ] 正式上線前 paper trading ≥ 3 個月，驗證實單滑點與執行差異

---

# PART 2 — ⚪ 原始多功能儀表板（舊系統，簡單帶過）

> 這是專案最初的版本：一個**功能很多但多數未經嚴格回測**的美股分析儀表板。
> 已凍結保留、**不是現在的交易主力**，這裡只做概覽。要用新策略請看 PART 1。

**進場頁面**：`Web_Dashboard/index.html`（11 分頁）＋ `script.js` / `style.css`
**啟動**：`python start_dashboard.py`（會一次跑齊下面所有掃描器與分析模組）

**主要分頁／功能**（多數已被新系統取代或判定為非必要）：
- 今日交易計劃、持倉管理、即時儀表板
- TD9 陣列熱力圖、產業板塊強弱、暴風動能選股、策略篩選器
- 綜合戰力評分（Power Gauge）、AI 深度投研、系統警報紀錄、參數設定

**對應的後端模組**（概覽，非主策略核心）：
| 類別 | 檔案 |
|------|------|
| 掃描器 | `us_scanner_hod.py`（HOD 當沖，用戶確認有效保留）、`us_scanner_td9/ma/orb/mtf.py`、`us_momentum_scanner.py` |
| 分析模組 | `generate_power_gauge.py`、`generate_ai_report.py`、`generate_institutional.py`、`generate_fundamentals.py`、`valuation_engine.py` |
| 選擇權 | `options_pricing.py`、`analyze_call.py`、`us_scanner_buy_call.py`、`optimize_risk.py` |
| 資料／圖表 | `generate_chart_data.py`、`generate_screener.py`、`us_sector_history.py`、`sync_watchlist.py`、`live_scanner.py` |
| 啟動器 | `start_dashboard.py`（舊）、`run_all.py` |

> 這些模組與新策略**共用** `scanner_base.py`（資料下載／快取）與 `web_server.py`（伺服器），
> 但新策略的進出場邏輯**完全不依賴**上述任何舊模組。

---

## 授權與免責

個人研究專案。所有內容僅供學習與研究，**不構成投資建議**；
依此交易的盈虧由使用者自行承擔。市場有風險，務必先紙上交易驗證。
