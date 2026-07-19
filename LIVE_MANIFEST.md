# LIVE_MANIFEST — 承重牆清單(改這些檔案前先跑 `python smoke_live.py`)

> repo 有 160+ 個 py 檔,其中絕大多數是**研究殘骸(research_*/optimize_*/compare_*/backtest_*)**,
> 改壞了不影響錢。真正每天在跑、產出你下單依據的只有下面這些。**動這些=動 live。**

## 每日排程執行(.github/workflows/pages.yml,09:00/21:00/22:30 台灣)
| 檔案 | 產出 | 用途 |
|---|---|---|
| core_status.py | core_status.json | 美股 5 指數核心訊號(200MA+信用哨+RiskTarget+vol-timing) |
| taiwan_status.py | taiwan_status.json | 台股版核心訊號 |
| leaders_status.py | leaders_status.json | 台美龍頭個股(衛星) |
| generate_kbar_annotations.py | kbar_annotations.json | 每根K棒 PIT 標籤 + 診斷 + 自適應 + review |
| generate_signals.py | strategy_signals.json | 個股衛星進場信號(S&P500 掃描) |
| aggressive_status.py | aggressive_status.json | 進取模式 TQQQ/SOXL(獨立二元哨;2026-07-19 補進排程) |

## 上述檔案的 import 依賴(間接承重)
scanner_base.py · filter_experiments.py · exit_experiments.py · rs_selection.py · validate_universe.py
(taiwan_status 被 generate_kbar_annotations import:TW_PARAMS/clean)

## 前端與本機工具
Web_Dashboard/strategy_dashboard.html(線上入口;index.html 部署時被覆蓋成轉址)
Web_Dashboard/index.html + script.js(本機掃描器)· update_dashboard.py · web_server.py
.github/workflows/pages.yml(排程與部署本身)

## 鐵則
1. 改任何上表檔案後:`python smoke_live.py` 全綠才准 commit。
2. 研究實驗一律開新 research_*.py,不准直接改 live 檔「順便試」。
3. 標籤/文字層(advisory)與機制層(進出場/倉位公式)分開審:機制層改動必須有回測背書。
