@echo off
REM ============================================================
REM SoundBeam - one-click trigger GitHub Actions release build
REM
REM   Usage:
REM     release.bat            -> read versionName from the committed
REM                              android/app/build.gradle.kts and use
REM                              v<versionName> as the tag (e.g. v0.1.0)
REM     release.bat v0.1.1     -> use that exact tag
REM
REM   Steps: move tag to HEAD -> push -> trigger workflow_dispatch
REM   Nothing project-specific is hardcoded: the repo URL is read
REM   from the git remote via gh, so this script keeps no private info.
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM ---- resolve tag: prefer explicit arg, else read committed versionName ----
set "TAG=%~1"
if not defined TAG (
  for /f "tokens=3" %%V in ('git show HEAD:android/app/build.gradle.kts ^| findstr /i "versionName"') do set "VER=%%V"
  set "VER=!VER:"=%!"
  if not defined VER (
    echo [ERROR] Could not read versionName from android/app/build.gradle.kts
    echo         Pass a tag explicitly, e.g.  release.bat v0.1.1
    pause
    exit /b 1
  )
  set "TAG=v!VER!"
)
echo Target tag: %TAG%

echo [1/4] Checking gh login status...
gh auth status >nul 2>&1
if errorlevel 1 (
  echo   [ERROR] gh is not logged in. Run: gh auth login
  pause
  exit /b 1
)
echo   OK.

echo [2/4] Moving tag %TAG% to current commit (HEAD)...
git tag -f %TAG% 2>&1
if errorlevel 1 (
  echo   [ERROR] Failed to move tag.
  pause
  exit /b 1
)
echo   OK.

echo [3/4] Pushing code and tag to GitHub...
git push origin HEAD 2>&1
git push -f origin %TAG% 2>&1
if errorlevel 1 (
  echo   [ERROR] Push failed. Check network / SSH key.
  pause
  exit /b 1
)
echo   OK.

echo [4/4] Triggering release workflow...
gh workflow run release.yml --ref %TAG% 2>&1
if errorlevel 1 (
  echo   [ERROR] Trigger failed. Make sure release.yml exists.
  pause
  exit /b 1
)

REM ---- repo URL derived from gh (keeps the script free of hardcoded info) ----
set "REPOURL="
for /f "usebackq tokens=1" %%u in (`gh repo view --json url --jq .url 2^>nul`) do set "REPOURL=%%u"

echo.
echo ============================================================
echo  Release build triggered for %TAG%!
echo  Check progress at: %REPOURL%/actions
echo ============================================================
pause
