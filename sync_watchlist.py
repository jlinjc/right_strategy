# -*- coding: utf-8 -*-
"""
sync_watchlist.py - 美股監控清單與產業板塊一鍵同步工具
===================================================
如果您未來想要調整、新增或刪除追蹤的股票，只需修改此檔案頂部的 `SECTOR_CONFIG`，
然後執行：
    python sync_watchlist.py

本工具會自動將修改同步更新至：
  1. scanner_base.py (後端預設清單)
  2. backtest_engine.py (風控回測板塊)
  3. us_sector_history.py (板塊資金輪動)
  4. us_momentum_scanner.py (備用選股清單)
  5. Web_Dashboard/script.js (前端網頁呈現)

同步後會自動下載全新 K 線資料並重算所有分析指標。
"""

import os
import re
import sys
import subprocess

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# ==============================================================================
# 🎯 核心配置區：未來您只要修改這裡即可！
# ==============================================================================
SECTOR_CONFIG = {
    "先進半導體與封裝 (AI Semiconductors & Packaging)": {
        "key": "semiconductor",
        "color": "#ef4444",
        "stocks": ["NVDA", "AMD", "TSM", "AVGO", "ARM"]
    },
    "矽光子與高速光通信 (Silicon Photonics & Optics)": {
        "key": "optics_connect",
        "color": "#f97316",
        "stocks": ["COHR", "LITE", "CLS", "FN", "CAMT"]
    },
    "AI伺服器與高速存儲 (AI Servers & Storage)": {
        "key": "servers_storage",
        "color": "#f59e0b",
        "stocks": ["SMCI", "DELL", "ANET", "PSTG", "WDC"]
    },
    "液冷基建與精密空調 (Cooling & HVAC Infrastructure)": {
        "key": "cooling_infra",
        "color": "#06b6d4",
        "stocks": ["VRT", "MOD", "FIX", "EME", "JCI"]
    },
    "AI電力、核能與SMR (AI Power & Grid & SMR)": {
        "key": "power_grid",
        "color": "#10b981",
        "stocks": ["CEG", "VST", "GEV", "ETN", "SMR"]
    },
    "AI軟體、智慧代理與超大市值 (AI SaaS & Hyperscalers)": {
        "key": "cloud_software",
        "color": "#3b82f6",
        "stocks": ["PLTR", "APP", "MSFT", "GOOGL", "META"]
    },
    "減肥藥與生技巨頭 (GLP-1 Weight Loss & Biotech)": {
        "key": "biotech_glp1",
        "color": "#ec4899",
        "stocks": ["LLY", "NVO", "VKTX", "TMDX", "CRSP"]
    },
    "低軌衛星與太空軍工 (Space & Satellites & Defense)": {
        "key": "space_defense",
        "color": "#8b5cf6",
        "stocks": ["RKLB", "LUNR", "ASTS", "GE", "LMT"]
    },
    "自動駕駛與智慧機器人 (Autonomous & Robotics)": {
        "key": "robotics_autonomous",
        "color": "#64748b",
        "stocks": ["TSLA", "UBER", "SYM", "ISRG", "ROK"]
    },
    "網路安全與未來金融科技 (Cybersecurity & Fintech & Crypto)": {
        "key": "cybersecurity_fintech",
        "color": "#a855f7",
        "stocks": ["CRWD", "PANW", "NET", "COIN", "HOOD"]
    }
}
# ==============================================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def get_flat_stocks():
    stocks = []
    for info in SECTOR_CONFIG.values():
        stocks.extend(info["stocks"])
    return stocks

def sync_scanner_base():
    filepath = os.path.join(SCRIPT_DIR, "scanner_base.py")
    if not os.path.exists(filepath): return
    print("  → 同步更新 scanner_base.py...")
    
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # 構造替換內容
    stocks_str = "    # 1. 先進半導體與封裝 (AI Semiconductors & Packaging)\n"
    idx = 1
    for sector_name, info in SECTOR_CONFIG.items():
        stocks_line = ", ".join(f'"{s}"' for s in info["stocks"])
        stocks_str += f'    # {idx}. {sector_name}\n'
        stocks_str += f'    {stocks_line},\n'
        idx += 1
        
    pattern = r"(_DEFAULT_STOCKS = \[\n)(.*?)(\n\])"
    replacement = f"_DEFAULT_STOCKS = [\n{stocks_str.rstrip()}\n]"
    new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_content)

def sync_backtest_engine():
    filepath = os.path.join(SCRIPT_DIR, "backtest_engine.py")
    if not os.path.exists(filepath): return
    print("  → 同步更新 backtest_engine.py...")
    
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
        
    map_str = "        self._sector_map = {\n"
    for sector_name, info in SECTOR_CONFIG.items():
        key = info["key"]
        stocks_line = ", ".join(f"'{s}'" for s in info["stocks"])
        map_str += f"            '{key}': [{stocks_line}],\n"
    map_str += "        }"
    
    pattern = r"(self\._sector_map = \{)(.*?)(\n\s*\})"
    new_content = re.sub(pattern, map_str, content, flags=re.DOTALL)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_content)

def sync_us_sector_history():
    filepath = os.path.join(SCRIPT_DIR, "us_sector_history.py")
    if not os.path.exists(filepath): return
    print("  → 同步更新 us_sector_history.py...")
    
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
        
    # 替換 SECTOR_MAP
    map_str = "SECTOR_MAP = {\n"
    for sector_name, info in SECTOR_CONFIG.items():
        stocks_line = ", ".join(f'"{s}"' for s in info["stocks"])
        map_str += f'    "{sector_name}": [{stocks_line}],\n'
    map_str += "}"
    
    pattern_map = r"(SECTOR_MAP = \{)(.*?)(\n\})"
    content = re.sub(pattern_map, map_str, content, flags=re.DOTALL)
    
    # 替換 SECTOR_COLORS
    color_str = "SECTOR_COLORS = {\n"
    for sector_name, info in SECTOR_CONFIG.items():
        color = info["color"]
        color_str += f'    "{sector_name}": "{color}",\n'
    color_str += "}"
    
    pattern_color = r"(SECTOR_COLORS = \{)(.*?)(\n\})"
    new_content = re.sub(pattern_color, color_str, content, flags=re.DOTALL)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_content)

def sync_us_momentum_scanner():
    filepath = os.path.join(SCRIPT_DIR, "us_momentum_scanner.py")
    if not os.path.exists(filepath): return
    print("  → 同步更新 us_momentum_scanner.py...")
    
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
        
    stocks = get_flat_stocks()
    stocks_lines = "    AI_TECH_STOCKS = [\n"
    for i in range(0, len(stocks), 5):
        chunk = stocks[i:i+5]
        line = ", ".join(f'"{s}"' for s in chunk)
        stocks_lines += f"        {line},\n"
    stocks_lines += "    ]"
    
    pattern = r"(except ImportError:\n\s*AI_TECH_STOCKS = \[\n)(.*?)(\n\s*\])"
    new_content = re.sub(pattern, f"except ImportError:\n{stocks_lines}", content, flags=re.DOTALL)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_content)

def sync_frontend_js():
    filepath = os.path.join(SCRIPT_DIR, "Web_Dashboard", "script.js")
    if not os.path.exists(filepath): return
    print("  → 同步更新 Web_Dashboard/script.js...")
    
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
        
    # 1. 替換 AI_TECH_STOCKS
    stocks = get_flat_stocks()
    stocks_lines = "const AI_TECH_STOCKS = [\n"
    for i in range(0, len(stocks), 5):
        chunk = stocks[i:i+5]
        line = ", ".join(f'"{s}"' for s in chunk)
        stocks_lines += f"    {line},\n"
    stocks_lines = stocks_lines.rstrip(",\n") + "\n];"
    
    pattern_stocks = r"(const AI_TECH_STOCKS = \[\n)(.*?)(\n\];)"
    content = re.sub(pattern_stocks, stocks_lines, content, flags=re.DOTALL)
    
    # 2. 替換 SECTOR_MAP
    map_str = "const SECTOR_MAP = {\n"
    for sector_name, info in SECTOR_CONFIG.items():
        stocks_line = ", ".join(f'"{s}"' for s in info["stocks"])
        map_str += f'    "{sector_name}": [{stocks_line}],\n'
    map_str = map_str.rstrip(",\n") + "\n};"
    
    pattern_map = r"(const SECTOR_MAP = \{)(.*?)(\n\};)"
    new_content = re.sub(pattern_map, map_str, content, flags=re.DOTALL)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_content)

def main():
    print("=" * 60)
    print(" 🛠️  Anti-Gravity 股票標的一鍵同步引擎")
    print("=" * 60)
    
    # 1. 同步所有配置
    sync_scanner_base()
    sync_backtest_engine()
    sync_us_sector_history()
    sync_us_momentum_scanner()
    sync_frontend_js()
    
    print("\n✅ 各模組代碼配置同步完成！")
    print("-" * 60)
    
    # 2. 自動執行更新數據流
    print("📊 正在執行全局 K 線資料下載...")
    subprocess.run([sys.executable, "generate_chart_data.py"], cwd=SCRIPT_DIR)
    
    print("📈 正在執行量化指標生成與回測計算管道...")
    pipeline_cmd = (
        "import subprocess, sys; "
        "subprocess.run([sys.executable, 'backtest_signals.py']); "
        "subprocess.run([sys.executable, 'us_sector_history.py']); "
        "subprocess.run([sys.executable, 'us_momentum_scanner.py']); "
        "subprocess.run([sys.executable, 'generate_power_gauge.py']); "
        "subprocess.run([sys.executable, 'trade_planner.py'])"
    )
    subprocess.run([sys.executable, "-c", pipeline_cmd], cwd=SCRIPT_DIR)
    
    print("\n🎉 同步暨全局數據計算完成！現在可以放心開啟 Dashboard 網頁了。")
    print("=" * 60)

if __name__ == "__main__":
    main()
