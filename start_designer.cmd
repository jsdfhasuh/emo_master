@echo off
setlocal
rem Use the installed environment; this launcher never installs packages.
if not defined EMO_MASTER_PYTHON set "EMO_MASTER_PYTHON=%USERPROFILE%\.conda\envs\emo_master\python.exe"
if not exist "%EMO_MASTER_PYTHON%" (
  echo Python not found: "%EMO_MASTER_PYTHON%"
  echo Set EMO_MASTER_PYTHON to the existing Python 3.10 environment's python.exe.
  if "%~1"=="" pause
  exit /b 1
)
"%EMO_MASTER_PYTHON%" "%~dp0scripts\dev.py" run-designer --local %*
set "LAUNCH_EXIT=%ERRORLEVEL%"
if not "%LAUNCH_EXIT%"=="0" (
  echo Designer exited with code %LAUNCH_EXIT%. See the error above.
  if "%~1"=="" pause
)
exit /b %LAUNCH_EXIT%
