"""
smoke_live.py — live 承重牆冒煙測試(改 LIVE_MANIFEST.md 所列檔案後必跑)
========================================================================
檢查:①live py 檔全部可編譯 ②可 import(抓語法外的頂層錯誤)③關鍵常數存在
④Web_Dashboard JSON 可解析且未過期太久(>3天=紅字警告)。全綠才准 commit。
用法: python smoke_live.py
"""
import os, sys, json, py_compile, importlib, traceback
from datetime import datetime
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

LIVE_PY = ['core_status.py', 'taiwan_status.py', 'leaders_status.py',
           'generate_kbar_annotations.py', 'generate_signals.py',
           'scanner_base.py', 'filter_experiments.py', 'exit_experiments.py',
           'rs_selection.py', 'validate_universe.py', 'update_dashboard.py']
LIVE_JSON = ['Web_Dashboard/core_status.json', 'Web_Dashboard/taiwan_status.json',
             'Web_Dashboard/kbar_annotations.json']
IMPORT_CHECK = ['core_status', 'taiwan_status']   # 只 import 無網路呼叫副作用的
FAILS = []


def ok(msg): print(f"  ✅ {msg}")


def bad(msg):
    print(f"  ❌ {msg}"); FAILS.append(msg)


print("=" * 64)
print("  smoke_live — live 承重牆冒煙測試")
print("=" * 64)

print("\n[1] 編譯檢查")
for f in LIVE_PY:
    if not os.path.exists(f):
        bad(f"{f} 不存在"); continue
    try:
        py_compile.compile(f, doraise=True); ok(f)
    except Exception as e:
        bad(f"{f} 編譯失敗: {e}")

print("\n[2] import 檢查(頂層執行不炸)")
for m in IMPORT_CHECK:
    try:
        mod = importlib.import_module(m); ok(m)
    except Exception:
        bad(f"{m} import 失敗:\n{traceback.format_exc()}")

print("\n[3] 關鍵常數存在(防止重構時默默弄丟)")
try:
    import core_status as C
    for k in ['PARAMS', 'VOL_TIMING', 'CREDIT_TICKERS', 'RISK_MULT', 'PANIC_VIX']:
        (ok if hasattr(C, k) else bad)(f"core_status.{k}")
    for tk in ['SMH', 'QQQ', 'SPY']:
        (ok if tk in C.PARAMS else bad)(f"PARAMS['{tk}']")
except Exception as e:
    bad(f"常數檢查失敗: {e}")

print("\n[4] JSON 可解析 + 新鮮度")
for f in LIVE_JSON:
    try:
        with open(f, encoding='utf-8') as fh:
            d = json.load(fh)
        ts = d.get('last_updated') or d.get('generated') or ''
        try:
            age_d = (datetime.now() - datetime.strptime(ts[:19], '%Y-%m-%d %H:%M:%S')).days
            (ok if age_d <= 3 else bad)(f"{f}(更新於 {ts},{age_d} 天前)")
        except Exception:
            ok(f"{f}(無法解析時間戳 {ts!r},僅驗格式)")
    except Exception as e:
        bad(f"{f} 解析失敗: {e}")

print("\n" + "=" * 64)
if FAILS:
    print(f"  🔴 {len(FAILS)} 項失敗 — 修好前別 commit:")
    for m in FAILS:
        print(f"     - {m.splitlines()[0]}")
    sys.exit(1)
print("  🟢 全綠 — live 承重牆完好,可以 commit。")
