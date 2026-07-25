@echo off
setlocal
cd /d "%~dp0"
set "APF_ZH="
findstr /I /R /C:"locale.*zh" config.json >nul 2>&1
if not errorlevel 1 (
  set "APF_ZH=1"
  chcp 65001 >nul
)

if defined AI_PROJECT_FINDER_PYTHON (
  if exist "%AI_PROJECT_FINDER_PYTHON%" (
    "%AI_PROJECT_FINDER_PYTHON%" -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>&1
    if not errorlevel 1 goto run_custom
  )
)

where py >nul 2>&1
if not errorlevel 1 (
  py -3 -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>&1
  if not errorlevel 1 goto run_py
)

where python >nul 2>&1
if not errorlevel 1 (
  python -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>&1
  if not errorlevel 1 goto run_python
)

where python3 >nul 2>&1
if not errorlevel 1 (
  python3 -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>&1
  if not errorlevel 1 goto run_python3
)

if defined APF_ZH (
  echo AI Project Finder 需要 Python 3.10 或更高版本。
  echo 可以从 https://www.python.org/downloads/windows/ 下载，然后重新运行此文件。
) else (
  echo AI Project Finder needs Python 3.10 or newer.
  echo Download Python from https://www.python.org/downloads/windows/ and run this file again.
)
pause
exit /b 1

:run_custom
"%AI_PROJECT_FINDER_PYTHON%" app.py --open
exit /b %errorlevel%

:run_py
py -3 app.py --open
exit /b %errorlevel%

:run_python
python app.py --open
exit /b %errorlevel%

:run_python3
python3 app.py --open
exit /b %errorlevel%
