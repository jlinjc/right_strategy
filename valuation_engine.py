"""
valuation_engine.py - 專業級基本面估值運算引擎 (華爾街機構級 v2)
================================================================
核心修正 (vs v1):
  - 移除合理價的現價 clamp (循環論證修正)
  - FCF 不可用時 DCF 標記 N/A，權重重新分配
  - 成長率 G 多來源優先鏈 (分析師共識 > 季度 EPS > 營收 > PEG)
  - 同業比較加入 Damodaran 行業基準校準
  - EPS 無效時跳過模型而非猜測
  - 新增分析師目標價作為第四因子
  - 信心等級驅動的動態安全邊際 (15~25%)

包含以下核心演算法：
  1. 動態美債無風險利率下載及 CAPM 股權成本 (Ke) 計算
  2. 加權平均資金成本 (WACC) 模型
  3. 兩階段自由現金流折現模型 (Two-Stage WACC-DCF)
  4. 增長折現乘數模型 (Discounted Multiplier Model)
  5. 跨 Watchlist 同業多倍數比較模型 + 行業基準校準
  6. 分析師目標價共識因子
  7. 綜合估值融合、動態安全邊際與估值狀態判定
"""

import sys
if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import yfinance as yf
import numpy as np
import pandas as pd


# ============================================================
# 0. Damodaran 行業估值基準表 (2024-2025 更新)
#    來源: Aswath Damodaran, NYU Stern
#    https://pages.stern.nyu.edu/~adamodar/
#    用於校準 Peer Group 偏差
# ============================================================

INDUSTRY_BENCHMARKS = {
    # industry_keyword: (median_pe, median_ps, median_ev_ebitda)
    # 半導體
    'Semiconductors':               (22.0, 6.5, 18.0),
    'Semiconductor Equipment':      (25.0, 5.0, 20.0),
    # 軟體
    'Software - Infrastructure':    (35.0, 10.0, 25.0),
    'Software - Application':       (30.0, 8.0, 22.0),
    'Software':                     (32.0, 9.0, 23.0),
    # 互聯網/平台
    'Internet Content & Information': (25.0, 6.0, 18.0),
    'Internet Retail':              (30.0, 3.0, 20.0),
    # 電子商務
    'E-Commerce':                   (28.0, 2.5, 18.0),
    # 雲端/資訊服務
    'Information Technology Services': (25.0, 3.5, 16.0),
    'Cloud Computing':              (40.0, 12.0, 30.0),
    # 消費電子
    'Consumer Electronics':         (20.0, 3.0, 14.0),
    # 電信
    'Telecom Services':             (15.0, 1.8, 8.0),
    'Communication Equipment':      (18.0, 3.0, 12.0),
    # 汽車/EV
    'Auto Manufacturers':           (15.0, 1.0, 10.0),
    'Electrical Equipment':         (25.0, 3.0, 16.0),
    # 醫療/生技
    'Biotechnology':                (20.0, 8.0, 15.0),
    'Drug Manufacturers':           (18.0, 4.5, 14.0),
    'Medical Devices':              (25.0, 5.0, 18.0),
    'Healthcare':                   (20.0, 4.0, 14.0),
    # 金融
    'Banks':                        (10.0, 2.5, None),
    'Financial Services':           (15.0, 4.0, None),
    'Insurance':                    (12.0, 1.5, 10.0),
    # 能源
    'Oil & Gas':                    (10.0, 1.2, 6.0),
    'Renewable Energy':             (25.0, 3.0, 15.0),
    # 工業
    'Aerospace & Defense':          (22.0, 2.0, 14.0),
    'Industrial Products':          (18.0, 2.0, 12.0),
    # 消費
    'Retail':                       (18.0, 1.0, 10.0),
    'Restaurants':                  (22.0, 3.0, 14.0),
    'Entertainment':                (20.0, 3.0, 12.0),
    # 房地產
    'REIT':                         (15.0, 5.0, 18.0),
    'Real Estate':                  (15.0, 4.0, 15.0),
}

# Sector 層級的 Fallback 基準
SECTOR_BENCHMARKS = {
    'Technology':           (28.0, 7.0, 20.0),
    'Communication Services': (20.0, 4.0, 14.0),
    'Consumer Cyclical':    (20.0, 2.0, 12.0),
    'Consumer Defensive':   (20.0, 2.0, 12.0),
    'Healthcare':           (20.0, 4.5, 14.0),
    'Financial Services':   (13.0, 3.0, None),
    'Industrials':          (20.0, 2.0, 13.0),
    'Energy':               (10.0, 1.2, 6.0),
    'Basic Materials':      (14.0, 1.5, 8.0),
    'Real Estate':          (15.0, 4.0, 15.0),
    'Utilities':            (16.0, 2.5, 10.0),
}


def get_industry_benchmark(industry, sector):
    """
    查詢行業估值基準。
    優先精確匹配 industry，再嘗試模糊匹配，最後 fallback 到 sector。
    """
    # 1. 精確匹配
    if industry in INDUSTRY_BENCHMARKS:
        return INDUSTRY_BENCHMARKS[industry]

    # 2. 模糊匹配（包含關鍵字）
    industry_lower = (industry or '').lower()
    for key, val in INDUSTRY_BENCHMARKS.items():
        if key.lower() in industry_lower or industry_lower in key.lower():
            return val

    # 3. Sector fallback
    if sector in SECTOR_BENCHMARKS:
        return SECTOR_BENCHMARKS[sector]

    # 4. 全市場 fallback
    return (20.0, 4.0, 14.0)


# ============================================================
# 1. 資本成本與利率計算 (CAPM & WACC)
# ============================================================

def get_risk_free_rate():
    """
    自 10 年期美債收益率 (^TNX) 動態取得無風險利率。
    若下載失敗或數值異常，預設回傳 4.25% (0.0425)。
    """
    try:
        tnx = yf.Ticker("^TNX")
        price = tnx.info.get('regularMarketPrice')
        if not price:
            # 嘗試歷史 K 線獲取最新收盤價
            hist = tnx.history(period="5d")
            if not hist.empty:
                price = hist['Close'].iloc[-1]

        if price and 0.5 < price < 15.0:
            return price / 100.0
    except Exception as e:
        print(f"  ⚠️ 無法動態下載 ^TNX (10Y Treasury)，將使用預設利率 4.25%: {e}")
    return 0.0425


def calculate_capm_ke(beta, rf, erp=0.05):
    """
    計算股權成本 Ke = Rf + Beta * ERP。
    Ke 限制在 6.0% ~ 20.0% 之間，以防極端值影響折現。
    """
    b = float(beta) if beta is not None else 1.2
    ke = rf + b * erp
    return max(0.06, min(0.20, ke))


def calculate_wacc(market_cap, total_debt, ke, rf, tax_rate=0.21):
    """
    計算加權平均資金成本 (WACC)。
    債務成本 Kd 估計為 Rf + 1.5% (投資級信用利差)。
    WACC 限制在下限 6.0%。
    """
    mc = float(market_cap) if market_cap else 0.0
    debt = float(total_debt) if total_debt else 0.0
    total_val = mc + debt

    if total_val <= 0:
        return ke

    w_e = mc / total_val
    w_d = debt / total_val

    kd = rf + 0.015  # 科技股/大中型股平均信用利差

    wacc = w_e * ke + w_d * kd * (1 - tax_rate)
    return max(0.06, wacc)


# ============================================================
# 2. 成長率多來源推導 (Growth Rate Estimation)
# ============================================================

def estimate_growth_rate(data):
    """
    多來源優先鏈推導成長率 G（小數形式，如 0.15 = 15%）。

    優先級：
      1. 分析師共識 EPS 增長率 (earningsGrowth)
      2. 季度 EPS YoY 中位數（最近 4 季）
      3. 營收增長率 (revenueGrowth)
      4. PEG 反推 (pe_fwd / peg)
      5. 行業預設增長率

    回傳: (G, source_label)
    """
    # 1. 分析師共識 EPS 增長率
    earn_g = data.get('earn_growth')
    if earn_g is not None and earn_g > 0:
        G = earn_g / 100.0
        return (max(0.03, min(0.50, G)), "分析師共識 EPS Growth")

    # 2. 季度 EPS YoY 中位數
    quarters = data.get('quarters', [])
    eps_yoys = []
    for q in quarters:
        yoy = q.get('eps_yoy')
        if yoy is not None and -200 < yoy < 500:
            eps_yoys.append(yoy)
    if len(eps_yoys) >= 2:
        median_yoy = float(np.median(eps_yoys))
        if median_yoy > 0:
            G = median_yoy / 100.0
            return (max(0.03, min(0.50, G)), f"季度 EPS YoY 中位數 ({len(eps_yoys)}Q)")

    # 3. 營收增長率
    rev_g = data.get('rev_growth')
    if rev_g is not None and rev_g > 0:
        G = rev_g / 100.0
        return (max(0.03, min(0.50, G)), "營收 YoY Growth")

    # 4. PEG 反推
    pe_fwd = data.get('pe_fwd')
    peg = data.get('peg')
    if pe_fwd and peg and peg > 0:
        implied_g = (pe_fwd / peg) / 100.0
        if implied_g > 0:
            return (max(0.03, min(0.50, implied_g)), "PEG 反推")

    # 5. 行業預設
    industry = data.get('industry', '')
    sector = data.get('sector', 'Technology')
    industry_lower = (industry or '').lower()

    if 'semiconductor' in industry_lower:
        return (0.15, "半導體行業預設")
    elif 'software' in industry_lower or 'cloud' in industry_lower:
        return (0.18, "軟體行業預設")
    elif 'internet' in industry_lower:
        return (0.14, "互聯網行業預設")
    elif sector == 'Technology':
        return (0.12, "科技業預設")
    else:
        return (0.10, "全市場預設")


# ============================================================
# 3. 兩階段 DCF 折現模型 (Two-Stage WACC-DCF)
# ============================================================

def calculate_wacc_dcf(info, wacc, G):
    """
    兩階段自由現金流折現 (FCFF) 模型。
    - Stage 1: 將當前 FCF 依照成長率 G 複利增長 5 年，以 WACC 折現。
    - Stage 2: 永續價值以 g = 2.5% (長期 GDP 增長率) 增長，折現回今日。
    - 權益價值 = 企業價值 + 現金 - 債務。

    若無法取得有效 FCF，回傳 None（不猜測）。
    """
    # 1. 取得基準自由現金流 FCF0
    fcf = info.get('freeCashflow')
    mcap = info.get('marketCap') or 0
    price = info.get('price') or info.get('currentPrice') or 1.0

    # 唯一 Fallback: Operating Cashflow - CapEx (這是 FCF 的定義)
    if fcf is None or fcf <= 0:
        op_cash = info.get('operatingCashflow')
        capex = info.get('capitalExpenditure')
        if op_cash is not None and op_cash > 0:
            c = abs(capex) if capex is not None else 0
            fcf = op_cash - c

    # 如果 FCF 仍然無效，誠實回傳 None
    if fcf is None or fcf <= 0:
        return None

    # 2. 投影未來 5 年現金流
    pv_stage1 = 0.0
    current_fcf = fcf
    for t in range(1, 6):
        current_fcf = current_fcf * (1 + G)
        pv_stage1 += current_fcf / ((1 + wacc) ** t)

    # 3. 永續價值 (Terminal Value)
    g_perpetual = 0.025  # 2.5% 永續成長率

    # 避免 WACC <= g_perpetual 導致分母為負或零
    wacc_denominator = wacc
    if wacc_denominator <= g_perpetual + 0.005:
        wacc_denominator = g_perpetual + 0.015

    fcf_5 = current_fcf
    tv = (fcf_5 * (1 + g_perpetual)) / (wacc_denominator - g_perpetual)
    pv_tv = tv / ((1 + wacc) ** 5)

    # 4. 企業價值 (EV)
    enterprise_value = pv_stage1 + pv_tv

    # 5. 股權價值 (Equity Value)
    cash = info.get('totalCash') or 0.0
    debt = info.get('totalDebt') or 0.0
    equity_value = enterprise_value + cash - debt

    # 6. 每股價值計算
    shares = info.get('sharesOutstanding')
    if not shares or shares <= 0:
        if mcap > 0 and price > 0:
            shares = mcap / price
        else:
            return None  # 無法計算每股價值

    dcf_price = equity_value / shares
    return max(0.01, dcf_price) if dcf_price > 0 else None


# ============================================================
# 4. 折現乘數模型 (Discounted Multiplier Model)
# ============================================================

def calculate_discounted_multiplier(info, ke, G):
    """
    折現乘數估值模型。
    - 依據成長率給定動態 PEG 溢價。
    - 目標 PE = Target PEG * G * 100。
    - 12M 目標價 = Forward EPS * Target PE。
    - 以股權成本 Ke 折現回今天。

    若無有效 EPS，回傳 (None, None)。
    """
    # 1. 取得 EPS（不猜測）
    eps = info.get('forwardEps')
    if eps is None or eps <= 0:
        eps = info.get('eps_fwd')
    if eps is None or eps <= 0:
        eps = info.get('eps_ttm') or info.get('trailingEps')
    if eps is None or eps <= 0:
        return (None, None)  # 無法估值，誠實回傳

    # 2. 決定合理 Target PEG
    if G >= 0.25:
        target_peg = 1.4
    elif G >= 0.20:
        target_peg = 1.3
    elif G >= 0.15:
        target_peg = 1.15
    elif G >= 0.10:
        target_peg = 1.0
    else:
        target_peg = 0.85

    # 3. 計算 Target PE
    target_pe = target_peg * (G * 100.0)
    target_pe = max(12.0, min(50.0, target_pe))  # 限制在 12x ~ 50x（成熟公司也不應低於 12x）

    # 4. 12M 目標價，折現回今天
    target_price_12m = eps * target_pe
    discounted_price = target_price_12m / (1 + ke)
    return (max(0.01, discounted_price), target_pe)


# ============================================================
# 5. 同業比較模型 (Comparable Companies Analysis + 行業基準)
# ============================================================

def calculate_comparable_valuation(info, peer_median_pe, peer_median_ps,
                                   industry_bench_pe, industry_bench_ps, ke):
    """
    同業比較模型估值（增強版：混合 Watchlist 同業 + 行業基準）。
    - Watchlist Peer 中位數與行業基準各佔 50%，作為校準
    - PE 相對價 = Forward EPS * Blended Peer PE (折現 1 年)
    - PS 相對價 = Revenue Per Share * Blended Peer PS
    - 相對目標價 = 0.5 * PE 相對價 + 0.5 * PS 相對價

    若無 EPS 且無 Revenue，回傳 None。
    """
    # 1. 混合同業 PE/PS 與行業基準 (50/50 blend)
    blended_pe = 0.5 * peer_median_pe + 0.5 * industry_bench_pe
    blended_ps = 0.5 * peer_median_ps + 0.5 * industry_bench_ps

    has_pe_val = False
    has_ps_val = False
    pe_discounted_price = 0
    ps_price = 0

    # 2. PE 相對估值
    eps = info.get('forwardEps')
    if eps is None or eps <= 0:
        eps = info.get('eps_fwd')
    if eps is None or eps <= 0:
        eps = info.get('eps_ttm') or info.get('trailingEps')

    if eps is not None and eps > 0:
        pe_target_price = eps * blended_pe
        pe_discounted_price = pe_target_price / (1 + ke)
        has_pe_val = True

    # 3. PS 相對估值
    rev = info.get('totalRevenue')
    shares = info.get('sharesOutstanding')

    if rev and shares and shares > 0 and rev > 0:
        rev_per_share = rev / shares
        ps_price = rev_per_share * blended_ps
        has_ps_val = True

    # 4. 混合估值
    if has_pe_val and has_ps_val:
        peer_comp_price = 0.5 * pe_discounted_price + 0.5 * ps_price
    elif has_pe_val:
        peer_comp_price = pe_discounted_price
    elif has_ps_val:
        peer_comp_price = ps_price
    else:
        return None  # 無法計算

    return max(0.01, peer_comp_price)


# ============================================================
# 6. 分析師目標價共識因子
# ============================================================

def calculate_analyst_consensus(data):
    """
    從分析師目標價推導共識估值。
    - 使用中位目標價（mean target），折扣 analyst variance
    - 覆蓋分析師數量作為信心加權

    回傳: (consensus_price, analyst_count)
    若無分析師數據，回傳 (None, 0)。
    """
    target_mean = data.get('target')
    target_hi = data.get('target_hi')
    target_lo = data.get('target_lo')
    analysts = data.get('analysts') or 0
    price = data.get('price') or 1.0

    if target_mean is None or target_mean <= 0 or analysts < 3:
        return (None, analysts)

    # 如果有高低價，計算分析師估值的離散度
    if target_hi and target_lo and target_hi > target_lo > 0:
        spread = (target_hi - target_lo) / target_mean
        # 離散度越大，越向保守（低價）方向調整
        if spread > 0.6:
            # 離散度很大，取 mean 和 low 的中間值
            consensus = 0.5 * target_mean + 0.5 * target_lo
        elif spread > 0.4:
            # 中等離散度，略保守
            consensus = 0.7 * target_mean + 0.3 * target_lo
        else:
            # 分析師共識度高
            consensus = target_mean
    else:
        consensus = target_mean

    return (consensus, analysts)


# ============================================================
# 7. 信心等級與動態安全邊際
# ============================================================

def calculate_confidence(factor_prices, factor_names, data):
    """
    計算估值信心等級，基於:
    1. 各因子估值的離散度 (CV = std/mean)
    2. 可用因子的數量
    3. 數據品質（EPS 修正趨勢、分析師覆蓋）

    回傳: (confidence: str, margin_of_safety: float, confidence_details: dict)
    """
    valid_prices = [p for p in factor_prices if p is not None and p > 0]
    n_factors = len(valid_prices)

    if n_factors == 0:
        return ("VERY_LOW", 0.35, {
            "reason": "無可用估值因子，數據嚴重不足",
            "n_factors": 0,
            "cv": None,
        })

    if n_factors < 2:
        return ("LOW", 0.30, {
            "reason": "僅有 1 個估值因子可用，數據不足",
            "n_factors": n_factors,
            "cv": None,
        })

    mean_val = np.mean(valid_prices)
    std_val = np.std(valid_prices)
    cv = std_val / mean_val if mean_val > 0 else 1.0

    # 分析師覆蓋加分
    analysts = data.get('analysts') or 0
    eps_up = data.get('eps_up_30d', 0)
    eps_down = data.get('eps_down_30d', 0)

    # 判定信心等級
    if cv < 0.12 and n_factors >= 3 and analysts >= 10:
        confidence = "HIGH"
        mos = 0.12  # 12% MoS
        reason = f"各因子高度一致 (CV={cv:.2f})，分析師覆蓋充足 ({analysts}家)"
    elif cv < 0.20 and n_factors >= 3:
        confidence = "HIGH"
        mos = 0.15  # 15% MoS
        reason = f"各因子一致性良好 (CV={cv:.2f})"
    elif cv < 0.30:
        confidence = "MEDIUM"
        mos = 0.20  # 20% MoS
        reason = f"各因子有中度差異 (CV={cv:.2f})"
    elif cv < 0.45:
        confidence = "LOW"
        mos = 0.25  # 25% MoS
        reason = f"各因子分歧較大 (CV={cv:.2f})，估值不確定性高"
    else:
        confidence = "VERY_LOW"
        mos = 0.30  # 30% MoS
        reason = f"各因子嚴重分歧 (CV={cv:.2f})，估值極不確定"

    # EPS 修正趨勢加減分
    if eps_up > eps_down and eps_up >= 5:
        reason += " + EPS 上修趨勢正面"
    elif eps_down > eps_up and eps_down >= 5:
        reason += " + ⚠️ EPS 下修趨勢"
        if confidence == "HIGH":
            confidence = "MEDIUM"
            mos = 0.20

    return (confidence, mos, {
        "reason": reason,
        "n_factors": n_factors,
        "cv": round(cv, 3),
        "factor_names": factor_names,
    })


# ============================================================
# 8. 全 Watchlist 多因子估值整合與同業分析
# ============================================================

# 預設權重（所有因子皆可用時）
DEFAULT_WEIGHTS = {
    'dcf': 0.35,
    'multiplier': 0.25,
    'peer': 0.25,
    'analyst': 0.15,
}


def redistribute_weights(available_factors):
    """
    根據可用因子動態分配權重。
    不可用的因子權重按比例分配給其他因子。
    """
    total_available = sum(DEFAULT_WEIGHTS[f] for f in available_factors)
    if total_available <= 0:
        # 全部不可用（不應該發生）
        n = len(available_factors) if available_factors else 1
        return {f: 1.0 / n for f in available_factors}

    weights = {}
    for f in available_factors:
        weights[f] = DEFAULT_WEIGHTS[f] / total_available
    return weights


def enrich_watchlist_valuations(stocks_data):
    """
    接受完整的 Watchlist 數據字典，計算同業中位數，並為每檔個股注入專業估值明細。

    參數:
      stocks_data (dict): {'NVDA': { fundamentals }, 'TSM': { ... }}

    回傳:
      dict: 注入估值欄位後的數據字典
    """
    # 0. 下載美債無風險利率
    rf = get_risk_free_rate()
    erp = 0.05

    # 1. 建立 DataFrame 以利進行同業中位數分析
    peer_list = []
    for ticker, data in stocks_data.items():
        peer_list.append({
            'ticker': ticker,
            'sector': data.get('sector', 'Technology'),
            'industry': data.get('industry', 'N/A'),
            'pe_fwd': data.get('pe_fwd'),
            'ps': data.get('ps'),
            'ev_ebitda': data.get('ev_ebitda'),
        })
    df_peers = pd.DataFrame(peer_list)

    # 2. 逐股進行估值運算
    for ticker, data in stocks_data.items():
        price = data.get('price') or data.get('currentPrice') or 1.0
        beta = data.get('beta')
        mcap = data.get('mcap') or data.get('marketCap')

        debt = data.get('total_debt') or data.get('totalDebt') or 0.0
        cash = data.get('total_cash') or data.get('totalCash') or 0.0
        shares = data.get('sharesOutstanding')
        revenue = data.get('rev_ttm') or data.get('totalRevenue')

        # 3. 計算股權成本 Ke 與加權平均資金成本 WACC
        ke = calculate_capm_ke(beta, rf, erp)
        wacc = calculate_wacc(mcap, debt, ke, rf)

        # 4. 多來源推導成長率 G
        G, g_source = estimate_growth_rate(data)

        # 5. 計算 DCF 估值
        dcf_info = {
            'freeCashflow': data.get('fcf') or data.get('freeCashflow'),
            'totalRevenue': revenue,
            'marketCap': mcap,
            'price': price,
            'totalCash': cash,
            'totalDebt': debt,
            'sharesOutstanding': shares,
            'operatingCashflow': data.get('operatingCashflow'),
            'capitalExpenditure': data.get('capitalExpenditure'),
        }
        price_dcf = calculate_wacc_dcf(dcf_info, wacc, G)

        # 6. 計算折現乘數估值
        multiplier_info = {
            'forwardEps': data.get('eps_fwd') or data.get('forwardEps'),
            'eps_fwd': data.get('eps_fwd'),
            'eps_ttm': data.get('eps_ttm'),
            'trailingEps': data.get('eps_ttm'),
            'price': price,
        }
        price_multiplier, target_pe = calculate_discounted_multiplier(multiplier_info, ke, G)

        # 7. 篩選同業並計算 CCA 相對估值
        sector = data.get('sector', 'Technology')
        industry = data.get('industry', 'N/A')

        # 7a. Watchlist 同業篩選
        peers_subset = df_peers[(df_peers['industry'] == industry) & (df_peers['ticker'] != ticker)]
        if len(peers_subset) < 3:
            peers_subset = df_peers[(df_peers['sector'] == sector) & (df_peers['ticker'] != ticker)]
        if len(peers_subset) < 3:
            peers_subset = df_peers[df_peers['ticker'] != ticker]

        # 計算同業 PE/PS 中位數
        valid_pe = peers_subset['pe_fwd'].dropna()
        valid_pe = valid_pe[valid_pe > 0]
        peer_median_pe = float(valid_pe.median()) if not valid_pe.empty else None

        valid_ps = peers_subset['ps'].dropna()
        valid_ps = valid_ps[valid_ps > 0]
        peer_median_ps = float(valid_ps.median()) if not valid_ps.empty else None

        # 7b. 行業基準 (Damodaran)
        bench_pe, bench_ps, bench_ev_ebitda = get_industry_benchmark(industry, sector)

        # 若 Watchlist 同業不可用，完全使用行業基準
        if peer_median_pe is None:
            peer_median_pe = bench_pe
        if peer_median_ps is None:
            peer_median_ps = bench_ps

        # 計算同業比較估值
        cca_info = {
            'forwardEps': data.get('eps_fwd'),
            'eps_fwd': data.get('eps_fwd'),
            'eps_ttm': data.get('eps_ttm'),
            'totalRevenue': revenue,
            'sharesOutstanding': shares,
            'price': price,
        }
        price_peer = calculate_comparable_valuation(
            cca_info, peer_median_pe, peer_median_ps,
            bench_pe, bench_ps, ke
        )

        # 8. 分析師目標價共識
        analyst_price, analyst_count = calculate_analyst_consensus(data)

        # 9. 動態權重分配（根據可用因子）
        available = {}
        factor_prices = []
        factor_names = []

        if price_dcf is not None:
            available['dcf'] = price_dcf
            factor_prices.append(price_dcf)
            factor_names.append('DCF')
        if price_multiplier is not None:
            available['multiplier'] = price_multiplier
            factor_prices.append(price_multiplier)
            factor_names.append('乘數')
        if price_peer is not None:
            available['peer'] = price_peer
            factor_prices.append(price_peer)
            factor_names.append('同業')
        if analyst_price is not None:
            available['analyst'] = analyst_price
            factor_prices.append(analyst_price)
            factor_names.append('分析師')

        if not available:
            # 所有模型都無法計算（極端情況）
            data['valuation'] = {
                'status': 'UNAVAILABLE',
                'status_zh': '⚪ 估值不可用 (數據不足)',
                'reason': '所有估值模型所需數據均不可用',
            }
            continue

        # 動態分配權重
        weights = redistribute_weights(list(available.keys()))

        # 10. 加權融合合理價（不做任何現價 clamp）
        fair_value = sum(available[f] * weights[f] for f in available)

        # 11. 信心等級與動態安全邊際
        confidence, mos, conf_details = calculate_confidence(
            factor_prices, factor_names, data
        )

        # 12. 估值邊界
        cheap_price = fair_value * (1 - mos)          # 便宜買點
        expensive_price = fair_value * (1 + mos)      # 昂貴賣點

        # 13. 估值狀態判定
        if price <= cheap_price:
            status = "Underpriced"
            status_zh = "低估 (🟢 便宜買點)"
        elif price >= expensive_price:
            status = "Overpriced"
            status_zh = "高估 (🔴 宜停利/觀望)"
        else:
            status = "Fair"
            status_zh = "合理 (🟡 價值區間)"

        # 14. 與現價的差異百分比
        upside_to_fair = round((fair_value / price - 1) * 100, 1) if price > 0 else 0
        upside_to_cheap = round((cheap_price / price - 1) * 100, 1) if price > 0 else 0

        # 15. 極端偏離警告（不 clamp，但標記）
        divergence_flag = None
        if fair_value > price * 3:
            divergence_flag = f"⚠️ 合理價遠高於現價 ({upside_to_fair:+.0f}%)，DCF 可能過度樂觀"
        elif fair_value < price * 0.3:
            divergence_flag = f"⚠️ 合理價遠低於現價 ({upside_to_fair:+.0f}%)，基本面可能惡化"

        # 寫入估值字典
        data['valuation'] = {
            # 資本成本
            'rf': round(rf * 100, 2),                    # %
            'ke': round(ke * 100, 2),                    # %
            'wacc': round(wacc * 100, 2),                # %

            # 成長率
            'implied_growth': round(G * 100, 1),         # %
            'growth_source': g_source,

            # 各因子估值
            'price_dcf': round(price_dcf, 2) if price_dcf else None,
            'price_multiplier': round(price_multiplier, 2) if price_multiplier else None,
            'target_pe': round(target_pe, 1) if target_pe else None,
            'price_peer': round(price_peer, 2) if price_peer else None,
            'price_analyst': round(analyst_price, 2) if analyst_price else None,
            'analyst_count': analyst_count,

            # 同業基準
            'peer_median_pe': round(peer_median_pe, 1),
            'peer_median_ps': round(peer_median_ps, 2),
            'industry_bench_pe': bench_pe,
            'industry_bench_ps': bench_ps,
            'industry_bench_ev_ebitda': bench_ev_ebitda,

            # 權重
            'weights_used': {k: round(v, 3) for k, v in weights.items()},
            'n_factors': len(available),

            # 綜合估值
            'fair_value': round(fair_value, 2),
            'cheap_price': round(cheap_price, 2),
            'expensive_price': round(expensive_price, 2),
            'upside_to_fair': upside_to_fair,
            'upside_to_cheap': upside_to_cheap,

            # 狀態與信心
            'status': status,
            'status_zh': status_zh,
            'confidence': confidence,
            'margin_of_safety_pct': round(mos * 100, 1),
            'confidence_details': conf_details,
            'divergence_flag': divergence_flag,
        }

    return stocks_data


# ============================================================
# 9. 自我測試主程式
# ============================================================
if __name__ == '__main__':
    print("🧪 啟動 valuation_engine v2 自我單元測試...")

    # Mock NVDA 數據 (正常 FCF 公司)
    mock_data = {
        'NVDA': {
            'sector': 'Technology',
            'industry': 'Semiconductors',
            'price': 120.0,
            'beta': 2.2,
            'mcap': 3000000000000,
            'pe_fwd': 25.0,
            'ps': 20.0,
            'peg': 0.8,
            'eps_fwd': 4.8,
            'eps_ttm': 3.5,
            'rev_ttm': 80000000000,
            'rev_growth': 80.0,
            'earn_growth': 100.0,
            'fcf': 30000000000,
            'sharesOutstanding': 25000000000,
            'totalCash': 40000000000,
            'totalDebt': 10000000000,
            'operatingCashflow': 35000000000,
            'capitalExpenditure': -5000000000,
            'target': 160.0,
            'target_hi': 200.0,
            'target_lo': 120.0,
            'analysts': 45,
            'eps_up_30d': 20,
            'eps_down_30d': 3,
            'quarters': [
                {'eps_yoy': 120.0}, {'eps_yoy': 95.0},
                {'eps_yoy': 80.0}, {'eps_yoy': 110.0},
            ],
        },
        # Mock TSM 數據 (正常公司)
        'TSM': {
            'sector': 'Technology',
            'industry': 'Semiconductors',
            'price': 160.0,
            'beta': 1.2,
            'mcap': 800000000000,
            'pe_fwd': 18.0,
            'ps': 8.0,
            'peg': 1.2,
            'eps_fwd': 9.0,
            'eps_ttm': 7.0,
            'rev_ttm': 75000000000,
            'rev_growth': 25.0,
            'earn_growth': 30.0,
            'fcf': 15000000000,
            'sharesOutstanding': 5100000000,
            'totalCash': 25000000000,
            'totalDebt': 15000000000,
            'operatingCashflow': 30000000000,
            'capitalExpenditure': -15000000000,
            'target': 200.0,
            'target_hi': 240.0,
            'target_lo': 170.0,
            'analysts': 38,
            'eps_up_30d': 12,
            'eps_down_30d': 5,
            'quarters': [
                {'eps_yoy': 35.0}, {'eps_yoy': 28.0},
                {'eps_yoy': 22.0}, {'eps_yoy': 30.0},
            ],
        },
        # Mock CRWD 數據 (FCF 偏低的高成長 SaaS)
        'CRWD': {
            'sector': 'Technology',
            'industry': 'Software - Infrastructure',
            'price': 350.0,
            'beta': 1.0,
            'mcap': 85000000000,
            'pe_fwd': 60.0,
            'ps': 22.0,
            'peg': 2.5,
            'eps_fwd': 5.8,
            'eps_ttm': 3.5,
            'rev_ttm': 3800000000,
            'rev_growth': 33.0,
            'earn_growth': 45.0,
            'fcf': 1200000000,
            'sharesOutstanding': 243000000,
            'totalCash': 3500000000,
            'totalDebt': 750000000,
            'operatingCashflow': 1500000000,
            'capitalExpenditure': -300000000,
            'target': 400.0,
            'target_hi': 450.0,
            'target_lo': 320.0,
            'analysts': 42,
            'eps_up_30d': 15,
            'eps_down_30d': 2,
            'quarters': [
                {'eps_yoy': 50.0}, {'eps_yoy': 40.0},
                {'eps_yoy': 35.0}, {'eps_yoy': 55.0},
            ],
        },
        # Mock 負 FCF 公司 (測試 DCF 跳過)
        'SNOW': {
            'sector': 'Technology',
            'industry': 'Software - Application',
            'price': 180.0,
            'beta': 1.5,
            'mcap': 60000000000,
            'pe_fwd': None,
            'ps': 18.0,
            'peg': None,
            'eps_fwd': -0.5,
            'eps_ttm': -1.2,
            'rev_ttm': 3300000000,
            'rev_growth': 28.0,
            'earn_growth': None,
            'fcf': -200000000,
            'sharesOutstanding': 333000000,
            'totalCash': 4000000000,
            'totalDebt': 0,
            'operatingCashflow': 100000000,
            'capitalExpenditure': -300000000,
            'target': 200.0,
            'target_hi': 250.0,
            'target_lo': 150.0,
            'analysts': 35,
            'eps_up_30d': 5,
            'eps_down_30d': 8,
            'quarters': [],
        },
    }

    enriched = enrich_watchlist_valuations(mock_data)

    for ticker, info in enriched.items():
        v = info.get('valuation', {})
        if v.get('status') == 'UNAVAILABLE':
            print(f"\n[{ticker}]")
            print(f"  ⚪ 估值不可用: {v.get('reason')}")
            continue

        print(f"\n[{ticker}] {info.get('industry', '')}")
        print(f"  現價: ${info['price']} | 綜合合理價: ${v['fair_value']}")
        print(f"  便宜買點: ${v['cheap_price']} | 昂貴賣點: ${v['expensive_price']}")
        print(f"  距合理價: {v['upside_to_fair']:+.1f}% | 距便宜價: {v['upside_to_cheap']:+.1f}%")
        print(f"  估值狀態: {v['status_zh']}")
        print(f"  信心等級: {v['confidence']} | 安全邊際: {v['margin_of_safety_pct']}%")
        print(f"  成長率: {v['implied_growth']}% ({v['growth_source']})")
        print(f"  Ke: {v['ke']}% | WACC: {v['wacc']}%")
        print(f"  ├ DCF: ${v['price_dcf']}" if v['price_dcf'] else "  ├ DCF: N/A (FCF 不可用)")
        print(f"  ├ 乘數: ${v['price_multiplier']}" if v['price_multiplier'] else "  ├ 乘數: N/A (EPS 不可用)")
        print(f"  ├ 同業: ${v['price_peer']}" if v['price_peer'] else "  ├ 同業: N/A")
        print(f"  └ 分析師: ${v['price_analyst']}" if v['price_analyst'] else "  └ 分析師: N/A")
        print(f"  權重: {v['weights_used']}")
        print(f"  行業基準 PE/PS: {v['industry_bench_pe']}/{v['industry_bench_ps']}")
        if v.get('divergence_flag'):
            print(f"  {v['divergence_flag']}")
        if v.get('confidence_details'):
            print(f"  信心詳情: {v['confidence_details']['reason']}")

    print("\n✅ valuation_engine v2 自我單元測試完成！")
