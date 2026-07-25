@echo off
setlocal
cd /d "%~dp0"

if defined LOCALAPPDATA (
  set "INSTALL_DIR=%LOCALAPPDATA%\AIProjectFinder"
) else (
  set "INSTALL_DIR=%USERPROFILE%\AppData\Local\AIProjectFinder"
)

if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
if errorlevel 1 (
  echo Unable to create the local launcher folder:
  echo %INSTALL_DIR%
  pause
  exit /b 1
)

> "%INSTALL_DIR%\ai-project-finder.bat" echo @echo off
>> "%INSTALL_DIR%\ai-project-finder.bat" echo call "%~dp0start.bat" %%*

echo Installed local launcher:
echo %INSTALL_DIR%\ai-project-finder.bat
echo.
echo Starting AI Project Finder...
call "%~dp0start.bat"
exit /b %errorlevel%
