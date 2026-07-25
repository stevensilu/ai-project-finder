@echo off
setlocal
cd /d "%~dp0"
set "APF_ZH="
findstr /I /R /C:"locale.*zh" config.json >nul 2>&1
if not errorlevel 1 (
  set "APF_ZH=1"
  chcp 65001 >nul
)

if defined LOCALAPPDATA (
  set "INSTALL_DIR=%LOCALAPPDATA%\AIProjectFinder"
) else (
  set "INSTALL_DIR=%USERPROFILE%\AppData\Local\AIProjectFinder"
)

if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
if errorlevel 1 (
  if defined APF_ZH (
    echo 无法创建本地启动目录：
  ) else (
    echo Unable to create the local launcher folder:
  )
  echo %INSTALL_DIR%
  pause
  exit /b 1
)

> "%INSTALL_DIR%\ai-project-finder.bat" echo @echo off
>> "%INSTALL_DIR%\ai-project-finder.bat" echo call "%~dp0start.bat" %%*

if defined APF_ZH (
  echo 已安装本地启动器：
) else (
  echo Installed local launcher:
)
echo %INSTALL_DIR%\ai-project-finder.bat
echo.
if defined APF_ZH (
  echo 正在启动 AI Project Finder...
) else (
  echo Starting AI Project Finder...
)
call "%~dp0start.bat"
exit /b %errorlevel%
