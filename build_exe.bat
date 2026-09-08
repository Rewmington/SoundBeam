@echo off
REM ============================================================
REM SoundBeam 电脑端打包脚本（生成单文件 exe）
REM 在项目根目录运行: build_exe.bat
REM 产物: dist\SoundBeam.exe
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"

echo [1/2] 检查依赖...
python -m pip install -r desktop\requirements.txt pyinstaller

echo [2/2] 打包...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name SoundBeam --paths . --collect-data ttkbootstrap ^
  --hidden-import pystray._win32 ^
  --icon assets\soundbeam.ico ^
  --add-binary "desktop\bthelper.exe;." ^
  desktop\launcher.py

echo.
echo 完成！exe 位于: dist\SoundBeam.exe
pause
