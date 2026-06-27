@echo off
chcp 65001 >nul
echo.
echo  移除所有「自動更新」排程...
echo.
schtasks /delete /tn "RightStrategyUpdate"    /f >nul 2>&1
schtasks /delete /tn "RightStrategyUpdate_AM" /f
schtasks /delete /tn "RightStrategyUpdate_PM" /f
echo.
echo  已移除(若顯示找不到代表本來就沒設,正常)。
echo.
pause
