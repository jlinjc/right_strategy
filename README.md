# Right Strategy — 右側動能拉回交易系統

一套以**數據驅動、逐步消除假設**方式定案的美股波段交易策略，核心思路來自
Minervini SEPA / O'Neil CANSLIM 的「右側動能 + 均線拉回」，並用兩年以上的
walk-forward 回測、敏感度分析、Monte Carlo 風險模擬與生存者偏差檢驗逐項驗證。

> ⚠️ 本專案為個人研究/實盤輔助工具，所有回測結果僅供參考，不構成投資建議。
> 帳面 Sharpe 建議打 7–8 折，正式投入前請先 paper trading ≥ 3 個月。

---

## 1. 策略總覽（最終定案版，2026-06-13）

### 1.1 股池選擇 — 系統化取代「手挑」
- 廣股池（109 檔，跨 AI/半導體/核能/傳產/防禦性板塊，去重）
- 每日計算**風險調整動能 RS（voladj = 6個月報酬 / 波動率）**，跨全股池排百分位
- 只允許交易 **RS 排名前 20%（≥80分）** 的個股
- 用規則重現「交易領導股」，避免回測使用「事後贏家股池」造成的生存者偏差

### 1.2 進場條件（四項，全部滿足）
1. **RS 排名 ≥ 80**（股池前 20% 最強股）
2. **均線拉回**：股價拉回觸及 21 日 EMA（Minervini 拉回買點）
3. **TTM 動能 > 0**（核心必備濾網，跨多空環境最穩定）
4. **不追高**：股價 < 1.08 × 10日均線（避免買在噴出末端）

> 基本面（淨利率、營收成長、ROE、前瞻 PE）**不做硬性篩選**——
> 經驗證 RS 動能已隱含吸收基本面資訊，硬篩會誤殺領導股。
> 改為在信號卡上「顯示」供裁量參考。

### 1.3 出場規則 — 分批出場（Ⓗ 方案）
1. 股價漲到 **進場價 + 3×ATR** 時，賣出 **50%** 倉位（落袋為安）
2. 剩餘 50% 用 **3.5×ATR 吊燈停損（Chandelier Exit）** 移動出場
3. 任何時候跌破**初始硬停損**立即出清
4. **最長持有 40 天**（時間停損，防止資金卡死在橫盤股）

### 1.4 風險控管
- 單筆風險 = 帳戶 **1%**
- 組合總風險上限（Portfolio Heat）= **3%**
- 單股持倉上限 = 帳戶 **20%**
- 同板塊持倉上限 = **3 檔**
- 系統化大盤 regime gate（QQQ>200MA 等）**經測試後否決不採用**——
  個股層的 RS+動能+均線濾網已內建市場環境判斷，疊加指數濾網反而拖累 Sharpe。
  大盤燈號僅作為「裁量式風險旋鈕」（轉空時手動減碼），不寫成硬規則。

### 1.5 誠實期望值（3 年 walk-forward，含修正期，已扣除生存者偏差）
| 指標 | 數值 |
|------|------|
| Sharpe | ~1.5–1.9 |
| Profit Factor | ~2.1–2.7 |
| 勝率 | ~38–43% |
| 回測最大回撤 | -11% ~ -14% |
| 規劃承受底線（含跳空/相關性風險） | **-25%** |

統計顯著性：360 筆交易，t = 5.95（p ≈ 0），已過約 36 種組態的多重測試門檻——
edge 是真實存在的，不是挑出來的雜訊。

---

## 2. 系統架構

```
┌─────────────────┐   ┌──────────────────┐   ┌────────────────────────┐
│ 回測/驗證工具群    │   │ 即時訊號產生        │   │ Web 作戰中心             │
│ backtest_engine  │──▶│ generate_signals  │──▶│ strategy_dashboard.html │
│ validate_*       │   │ rs_selection      │   │ web_server.py           │
│ compare_*        │   │ generate_evidence │   │                         │
└─────────────────┘   └──────────────────┘   └────────────────────────┘
```

### 2.1 核心引擎
| 檔案 | 說明 |
|------|------|
| `backtest_engine.py` | 事件驅動回測引擎：Portfolio、RiskManager（含 portfolio heat 閘門）、`scale_out()` 分批出場支援 |
| `backtest_strategies.py` | 策略類別集合：`MAPullbackStrategy`（主策略）、TD9、動能突破等（已驗證為冗餘，保留供對照） |
| `exit_manager.py` | 實盤持倉管理：載入/儲存持倉、出場條件判斷（含分批/吊燈/時間停損）、出場提醒 |
| `scanner_base.py` | 共用的股票資料下載/快取/技術指標基礎模組 |

### 2.2 即時訊號
| 檔案 | 說明 |
|------|------|
| `rs_selection.py` | 橫斷面 RS 排名（voladj 模式），輸出當日強勢股池 |
| `generate_signals.py` | 用定案策略（`FilteredScaledExit`）即時掃描，產出今日進場信號卡 + 大盤 regime + 觀察清單 → `strategy_signals.json` |
| `fundamentals_filter.py` / `generate_fundamentals.py` | 抓取/整理基本面資料供信號卡顯示（非硬篩） |
| `generate_evidence.py` | 匯出完整回測研究歷程 → `backtest_evidence.json`，供 dashboard「實證」面板顯示 |

### 2.3 驗證/實驗工具（研究歷程，可重跑驗證任何結論）
| 檔案 | 用途 |
|------|------|
| `validate_strategy.py` | 官方 walk-forward 驗證（PASS/CONDITIONAL/FAIL 裁決 + 敏感度網格） |
| `validate_robustness.py` | 統計顯著性（t檢定）+ Monte Carlo bootstrap 回撤分布 |
| `validate_universe.py` | 生存者偏差檢驗（中性跨板塊股池 vs AI 股池） |
| `compare_strategies.py` | 多策略單獨/混合績效比較（確認 MA 拉回 = 全部 edge 來源） |
| `compare_exits.py` / `exit_experiments.py` | 出場規則比較（基準線/吊燈/Fib/分批混合等） |
| `compare_filters.py` / `filter_experiments.py` | 進場濾網逐項加總測試（動能/不追高/放量/RS） |
| `compare_filters_fund.py` / `fundamentals_filter.py` | 基本面濾網污染測試（look-ahead 研究用，非實盤） |
| `compare_regime.py` | 系統化大盤 regime gate 測試（全部否決） |
| `compare_concentration.py` | 持倉集中度/組合熱度測試（確認 portfolio heat 上限有效） |
| `compare_rs.py` / `compare_rs_metric.py` | 橫斷面 RS 選股 + RS 指標定義比較（voladj 勝出） |

### 2.4 Web 作戰中心
| 檔案 | 說明 |
|------|------|
| `Web_Dashboard/strategy_dashboard.html` | 精簡單頁作戰中心：大盤 regime 燈號 / 策略紀律總覽 / 今日進場信號卡 / 觀察清單 / 回測實證 |
| `Web_Dashboard/index.html`, `script.js`, `style.css` | 舊版多分頁儀表板（掃描器、選擇權 Greeks、Power Gauge 等附加功能，保留但非主策略） |
| `web_server.py` | 本地伺服器 |
| `start_strategy.py` | 一鍵啟動：跑 `generate_signals.py` + `generate_evidence.py` → 啟動 `web_server.py` → 自動開啟作戰中心。`--data-only` 只更新資料不開站 |

### 2.5 其他輔助
| 檔案 | 說明 |
|------|------|
| `trade_planner.py` | 進場價/停損/分批目標/部位大小計算 |
| `options_pricing.py`, `generate_power_gauge.py`, `generate_institutional.py`, `generate_ai_report.py` | 舊版附加分析模組（選擇權 Greeks、機構持股、AI 報告等），非主策略核心 |
| `us_scanner_*.py` | 各類掃描器（HOD 當沖、MTF、TD9、ORB 等），其中 **HOD 當沖掃描為用戶確認有效並保留** |

---

## 3. 快速開始

```bash
# 安裝相依套件（自行依 requirements 調整，主要為 pandas / numpy / yfinance）
pip install pandas numpy yfinance

# 一鍵啟動作戰中心
python start_strategy.py
# → 瀏覽器自動開啟 http://localhost:8000/strategy_dashboard.html

# 只更新訊號資料，不開網頁
python start_strategy.py --data-only

# 重跑官方驗證
python validate_strategy.py --fast    # 2年快速版
python validate_strategy.py --3y      # 3年完整版
```

---

## 4. 策略演進歷程（重要決策摘要）

研究過程完整記錄於 `backtest_evidence.json`（由 `generate_evidence.py` 產生），
並顯示於作戰中心的「回測實證」面板。關鍵里程碑：

1. **三策略混合 → 單一 MA 拉回**：拆開測試發現 TD9 / 動能突破單獨皆為負 Sharpe，
   混入主策略反而拖累整體表現（Sharpe 2.58 → 3.00）。
2. **出場規則比較**：測試基準線/吊燈/Fibonacci/亞當鏡像/時間出場，
   最終選擇分批出場（Ⓗ：進場+3×ATR 賣 50% + 剩餘吊燈 3.5×ATR），
   在 Sharpe、勝率、PF、MDD 之間取得最佳平衡。
3. **進場濾網**：動能濾網（TTM Momentum > 0）為跨環境最穩定的真實 edge；
   不追高濾網在多頭環境額外加分；放量反彈濾網經測試為災難級負貢獻，否決。
4. **系統化大盤 regime gate**：全數否決，個股層濾網已隱含市場環境判斷。
5. **生存者偏差檢驗**：手挑 AI 股池在中性股池上測試 Sharpe ≈ 0，
   證明原始高 Sharpe 主要來自「股池選擇」而非進出場規則本身。
6. **橫斷面 RS 排名選股**：用規則化的相對強度排名重現「交易領導股」，
   把系統化版本的 Sharpe 從 ~1.0 拉回到 ~1.5–1.9，且無生存者偏差。
7. **RS 指標優化**：風險調整動能（voladj = 報酬/波動）在四項指標中全面最佳，
   因偏好平滑強勢股、規避動能崩盤風險，已設為預設。
8. **統計穩健性**：t = 5.95 高度顯著，Monte Carlo 模擬顯示相關性曝險是
   真實回撤主要來源 → 加入組合熱度上限（3%）有效改善 MDD。

---

## 5. 待辦事項

- [ ] 交易日誌寫入（目前持倉讀 `Web_Dashboard/positions.json`，尚無自動下單/日誌記錄）
- [ ] Forward PE 等基本面「當下篩選」（無乾淨歷史快照，無法回測，僅供當下裁量）
- [ ] 正式上線前 paper trading ≥ 3 個月，驗證實單滑點與執行差異
