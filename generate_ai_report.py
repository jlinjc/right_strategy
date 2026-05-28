"""
generate_ai_report.py - AI 深度投研報告產生器
================================================
使用 Google Gemini API 生成硬核半導體供應鏈深度分析報告。
將使用者定義的 Prompt 發送給 Gemini，並將回覆儲存為 Markdown 供前端展示。

用法:
  python generate_ai_report.py                    # 使用預設 prompt 生成報告
  python generate_ai_report.py --prompt "..."     # 使用自訂 prompt

依賴: google-genai
安裝: pip install google-genai
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_DIR = os.path.join(SCRIPT_DIR, 'Web_Dashboard')
REPORT_DIR = os.path.join(DASHBOARD_DIR, 'ai_reports')
REPORT_INDEX_PATH = os.path.join(REPORT_DIR, 'index.json')

# Gemini API 設定
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# 重試與備用模型設定
MAX_RETRIES = 3              # 每個模型最多重試次數
RETRY_BASE_DELAY = 5         # 初始重試等待秒數 (指數退避: 5s, 10s, 20s)
FALLBACK_MODELS = [           # 備用模型清單 (依優先序)
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
]

# ============================================================
# 預設 Prompt (使用者提供的硬核半導體投研模板)
# ============================================================
DEFAULT_PROMPT = """【角色與思維設定】
你是一位兼具 TechInsights 逆向工程思維、SemiAnalysis 硬核產業分析能力、以及頂尖晶圓廠設備副總級別技術視野的科技投研專家。你拒絕使用任何市場共識（Market Consensus）或券商公開報告的官話。請完全從「第一性原理（First Principles）」出發，專注於物理失效、良率殺手、以及二級供應鏈（Tier 3-5）中不可替代的單點崩潰節點。

【掃描目標範圍】
當前時間節點為 2026 年中，請針對以下前沿技術節點進行深度解構：
- 目標節點：2nm/A16 背面供電網路 (BSPDN) 的量產爬坡期
- 關聯技術：High-NA EUV 光阻缺陷、玻璃基板 TGV 製程、或 1.6T CPO 矽光子模組

【強制性排他條款（自動過濾雜訊）】
請完全跳過市場已充分定價（Fully Priced-in）的主流板塊與 Tier 1/2 巨頭。只要提及以下名詞，即視為任務失敗：
- 晶片端：NVIDIA、AMD、TSMC 常規產能、Intel。
- 記憶體端：常規 HBM3e/HBM4、常規 DDR5。
- 通用設備/組裝：常規 CoWoS 組裝產能、標準散熱水冷板、常規 800G 光模組。
你必須將視角強制拉低到大眾看不見的次級零部件、特種化學品配位基、精密光學元件加工。

【四維硬核交叉論證（AI 思考鏈約束）】
在評估「下一個供需失衡黑洞」時，你必須依序完成以下四個步驟的推論，並將其邏輯體現在最終輸出中：
1. 物理失效與良率殺手（Failure Mechanism）：指出在 2026 年該製程線上面臨的「核心物理極限」（例如：因應力導致的次表面微裂縫、高頻下的插入損耗衰減、原子層積厚度不均造成的熱電遷移崩潰）。不買這個材料/設備，良率就絕對是零。
2. 特殊設備產能天花板（Tool Bottleneck）：鎖定該材料生產或該製程實施中，上游最不可或缺的「單點壟斷機台或關鍵零組件」（例如：大功率雷射源、陶瓷空氣軸承主軸、毫秒級高速脈衝閥），並點出其 2026 年的設備交期（Lead Time）異常狀況。
3. 2026 環保法規與純度壁壘（Regulation & Purity）：結合 2026 年最新的歐美 PFAS（全氟和多氟烷基物質）環保禁令實施進度，評估替代綠色化學品的配方專利壁壘，或是純度必須達到幾 N（99.999%以上）的精煉剛需。
4. 晶圓廠微觀動向（Fab Micro-signals）：結合 ASML、TSMC、NVIDIA 最新技術論壇、法說會，或專業拆解報告中，提及率正在「斜率陡峭上升」的底層硬核技術名詞。

【輸出格式要求】
請精準篩選出符合上述所有條件的 3 個最迫切、最剛需的「隱形缺貨節點」，並嚴格按照以下結構輸出：

### 📌 節點 [X]：[精準的底層材料 / 特定次級設備 / 微型特殊零組件名稱]
- **1. 核心工程物理痛點（Why It's Critical）：** 詳細說明其對應的物理失效模式、化學反應方程式原理（若適用），以及為何大廠無法繞過它、不用它就無法量產的底層硬傷。
- **2. 產能卡脖子關鍵與壁壘（Where Is The Bottleneck）：** 點出全球產能集中在哪些未被大眾注意的 Tier 3-5 廠商？其技術護城河（如：加工精度、配方專利、設備交期）是什麼？
- **3. 台股與美股核心受惠隱形冠軍（Investment Targets）：**
  請分別提供「台股」與「美股」中，市值中小型、具備極高技術定價權的隱形冠軍（各市場至少 2 檔）。為了全面評估，必須以完整的 Markdown 表格呈現，嚴禁任何表格行數的省略（不可使用「略」、「同上」或省略號）。表格需嚴格包含以下欄位：
  | 股票代號/名稱 | 所屬市場 | 2026核心技術護城河（需具體到產品型號或獨家製程） | 2026實質營收與獲利動能（對應晶圓廠產線的具體訂單） | 替代風險評級 (高/中/低) |"""


def ensure_dirs():
    os.makedirs(REPORT_DIR, exist_ok=True)


def load_report_index():
    """載入報告索引"""
    if os.path.exists(REPORT_INDEX_PATH):
        try:
            with open(REPORT_INDEX_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {"reports": []}


def save_report_index(index_data):
    """儲存報告索引"""
    with open(REPORT_INDEX_PATH, 'w', encoding='utf-8') as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)


def _is_quota_exhausted(error):
    """判斷是否為免費額度用完 (不應重試，重試只會浪費額度)"""
    error_str = str(error)
    return 'RESOURCE_EXHAUSTED' in error_str or ('429' in error_str and 'limit: 0' in error_str)


def _is_retryable_error(error):
    """判斷錯誤是否值得重試 (503 伺服器暫時忙碌)"""
    error_str = str(error)
    # 額度用完不要重試
    if _is_quota_exhausted(error):
        return False
    return any(code in error_str for code in ['503', '429', 'UNAVAILABLE', 'overloaded'])


def _call_gemini_with_retry(client, model_name, prompt_text):
    """
    呼叫 Gemini API，帶有指數退避重試機制。
    回傳: (response_text, actual_model_used) 或拋出例外
    """
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"   🔄 嘗試 #{attempt}/{MAX_RETRIES} (模型: {model_name})...")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt_text,
            )
            if response.text:
                return response.text, model_name
            else:
                raise ValueError("Gemini 回傳了空的回覆")
        except Exception as e:
            last_error = e
            # 額度用完：直接停止，不浪費重試次數
            if _is_quota_exhausted(e):
                print(f"   🚫 免費 API 額度已用完，停止重試")
                raise
            elif _is_retryable_error(e) and attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))  # 5s, 10s, 20s
                print(f"   ⏳ 伺服器忙碌中 (嘗試 {attempt}/{MAX_RETRIES})，等待 {delay} 秒後重試...")
                time.sleep(delay)
            elif _is_retryable_error(e) and attempt == MAX_RETRIES:
                print(f"   ⚠️ 模型 {model_name} 已重試 {MAX_RETRIES} 次仍然忙碌")
                raise
            else:
                # 非重試型錯誤 (如 API key 無效、模型不存在)，直接拋出
                raise


def generate_report(prompt=None, custom_title=None):
    """
    呼叫 Gemini API 生成報告並存檔。
    內建自動重試 + 備用模型降級機制。
    回傳: { 'status': 'ok', 'filename': '...', 'title': '...' } 或 { 'status': 'error', 'message': '...' }
    """
    if not GEMINI_API_KEY:
        msg = "❌ 尚未設定 GEMINI_API_KEY。請在 .env 檔案中加入：GEMINI_API_KEY=你的金鑰"
        print(msg)
        return {"status": "error", "message": msg}

    prompt_text = prompt or DEFAULT_PROMPT
    ensure_dirs()

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 🧠 正在呼叫 Gemini ({GEMINI_MODEL}) 生成投研報告...")
    print(f"   Prompt 長度: {len(prompt_text)} 字元")

    try:
        from google import genai

        client = genai.Client(api_key=GEMINI_API_KEY)

        # 建立嘗試模型清單：主模型優先，然後是備用模型 (去除重複)
        models_to_try = [GEMINI_MODEL]
        for fb_model in FALLBACK_MODELS:
            if fb_model not in models_to_try:
                models_to_try.append(fb_model)

        report_content = None
        actual_model = GEMINI_MODEL
        last_error = None

        for model_name in models_to_try:
            try:
                report_content, actual_model = _call_gemini_with_retry(client, model_name, prompt_text)
                break  # 成功了，跳出迴圈
            except Exception as e:
                last_error = e
                # 額度用完：所有模型共用同一個額度，不用再試其他模型
                if _is_quota_exhausted(e):
                    print(f"   🚫 API 免費額度已用完，跳過其餘模型")
                    break
                elif _is_retryable_error(e) and model_name != models_to_try[-1]:
                    next_model = models_to_try[models_to_try.index(model_name) + 1]
                    print(f"   🔀 切換到備用模型: {next_model}")
                    continue
                else:
                    # 最後一個模型也失敗了，或者是不可重試的錯誤
                    break

        if not report_content:
            if last_error and _is_quota_exhausted(last_error):
                msg = (
                    "🚫 今日 Gemini API 免費額度已用完！\n"
                    "免費額度每天重置（台灣時間早上 8:00）。\n"
                    "請明天早上 8 點後再試，或到 Google Cloud Console 開啟付費方案即可無限制使用。"
                )
            elif last_error and _is_retryable_error(last_error):
                msg = (
                    f"⏳ Google Gemini 伺服器目前流量過大，所有模型都暫時無法回應。\n"
                    f"這是 Google 端的暫時性問題，不是你的設定有誤。\n"
                    f"建議等待 1~2 分鐘後再試一次！"
                )
            else:
                msg = f"❌ Gemini API 呼叫失敗: {str(last_error)}"
            print(msg)
            return {"status": "error", "message": msg}

        if actual_model != GEMINI_MODEL:
            print(f"   ℹ️ 注意：因主模型忙碌，本次報告由 {actual_model} 生成")

        print(f"   ✅ 報告生成完成，長度: {len(report_content)} 字元 (模型: {actual_model})")

        # 儲存報告
        now = datetime.now()
        date_str = now.strftime('%Y-%m-%d')
        time_str = now.strftime('%H%M%S')
        filename = f"report_{date_str}_{time_str}.md"
        filepath = os.path.join(REPORT_DIR, filename)

        # 建立報告標題
        title = custom_title or f"AI 深度投研報告 — {date_str}"

        # 在報告頂部加上元資料
        header = f"""---
title: {title}
date: {now.strftime('%Y-%m-%d %H:%M:%S')}
model: {actual_model}
---

"""
        full_content = header + report_content

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(full_content)

        print(f"   📄 報告已儲存: {filepath}")

        # 更新索引
        index = load_report_index()
        index["reports"].insert(0, {
            "filename": filename,
            "title": title,
            "date": now.strftime('%Y-%m-%d %H:%M:%S'),
            "model": actual_model,
            "prompt_preview": prompt_text[:200] + "..." if len(prompt_text) > 200 else prompt_text,
        })
        # 只保留最近 50 份報告
        index["reports"] = index["reports"][:50]
        save_report_index(index)

        return {
            "status": "ok",
            "filename": filename,
            "title": title,
            "date": now.strftime('%Y-%m-%d %H:%M:%S'),
            "content_length": len(report_content),
            "model_used": actual_model,
        }

    except ImportError:
        msg = "❌ 尚未安裝 google-genai 套件。請執行: pip install google-genai"
        print(msg)
        return {"status": "error", "message": msg}
    except Exception as e:
        msg = f"❌ Gemini API 呼叫失敗: {str(e)}"
        print(msg)
        return {"status": "error", "message": msg}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='AI 深度投研報告產生器')
    parser.add_argument('--prompt', type=str, default=None, help='自訂 Prompt (預設使用內建模板)')
    parser.add_argument('--title', type=str, default=None, help='自訂報告標題')
    args = parser.parse_args()

    result = generate_report(prompt=args.prompt, custom_title=args.title)
    if result['status'] == 'ok':
        print(f"\n✅ 報告生成成功！")
        print(f"   檔名: {result['filename']}")
        print(f"   標題: {result['title']}")
    else:
        print(f"\n❌ 報告生成失敗: {result['message']}")
