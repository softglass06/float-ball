@echo off
rem 构建单文件 exe（无控制台窗口）
cd /d "%~dp0"
pyinstaller --noconfirm --onefile --windowed --name FloatBall floatball.py
echo.
echo Build done. See dist\FloatBall.exe
pause
