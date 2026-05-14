import subprocess
import time
import sys

# 你所有的掃描器檔案名稱
scripts = [
    "live_scanner.py",
    "us_scanner_td9.py",
    "us_scanner_ma.py"
]

processes = []

print("🚀 準備啟動所有美股監控掃描器...")
print("===================================")

for script in scripts:
    print(f"👉 正在啟動: {script}")
    # 使用 subprocess 開啟新行程，讓它們同時在背景執行，並把輸出印在同一個終端機裡
    p = subprocess.Popen([sys.executable, script])
    processes.append(p)
    # 稍微停頓 2 秒，避免同時大量向 Yahoo Finance 發送請求導致被阻擋
    time.sleep(2)

print("===================================")
print("✅ 所有掃描器皆已成功啟動並在背景監控中！")
print("💡 提示：所有的文字報告會交錯顯示在這個視窗中。")
print("🛑 如果想要停止全部監控，請直接按鍵盤的 [Ctrl + C]。")
print("===================================\n")

try:
    # 讓主程式保持運行，等待子程式結束 (基本上會一直掛著)
    for p in processes:
        p.wait()
except KeyboardInterrupt:
    print("\n\n🛑 收到中斷指令，正在安全關閉所有掃描器...")
    for p in processes:
        p.terminate()
    print("👋 所有掃描器已成功關閉。")
