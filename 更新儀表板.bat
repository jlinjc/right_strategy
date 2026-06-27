@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  ============================================================
echo   一鍵更新儀表板:更新資料 -^> 啟動伺服器 -^> 自動開啟瀏覽器
echo  ============================================================
echo   注意:會另外彈出一個「伺服器小黑窗」,看儀表板期間別關它。
echo.
python update_dashboard.py
if errorlevel 1 (
  echo.
  echo  [!] python 失敗,改試 py ...
  py update_dashboard.py
)
echo.
echo  完成。瀏覽器應已開啟 http://localhost:8000/strategy_dashboard.html
echo  (這個視窗可以關掉;但「伺服器小黑窗」要留著)
echo.
pause
