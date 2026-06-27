@echo off
chcp 65001 >nul
set PYW=C:\Users\Jason\AppData\Local\Programs\Python\Python311\pythonw.exe
set SCR=C:\Users\Jason\right_strategy\update_dashboard.py
echo.
echo  設定自動更新時段:早上 07:00 + 晚上 21:00/21:30/22:00/22:30/23:00/23:30
echo  (晚上=美股盤中即時更新;早上=收盤定數。背景靜默跑,不彈瀏覽器)
echo.

REM 先清掉舊的(避免重複)
schtasks /delete /tn "RightStrategyUpdate"    /f >nul 2>&1
schtasks /delete /tn "RightStrategyUpdate_AM" /f >nul 2>&1
schtasks /delete /tn "RightStrategyUpdate_PM" /f >nul 2>&1

REM 早上 07:00(美股收盤後定數)
schtasks /create /tn "RightStrategyUpdate_AM" /tr "%PYW% %SCR% --no-open" /sc DAILY /st 07:00 /f

REM 晚上 21:00 起,每 30 分鐘重複,持續 2 小時 31 分 → 21:00 21:30 22:00 22:30 23:00 23:30
schtasks /create /tn "RightStrategyUpdate_PM" /tr "%PYW% %SCR% --no-open" /sc DAILY /st 21:00 /ri 30 /du 0002:31 /f

echo.
if errorlevel 1 (
  echo  [!] 設定失敗。請對此 bat 按右鍵「以系統管理員身分執行」再試一次。
) else (
  echo  [OK] 已設定 7 個自動更新時段。你打開 dashboard 就是最新數字。
  echo       移除:執行 移除每日排程.bat  /  改時間:編輯本檔 /st 與 /du 後再雙擊
)
echo.
pause
