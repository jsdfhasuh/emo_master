@echo off
setlocal
rem Source launcher only. A frozen Runtime uses run_operator.py as its entry.
if not defined EMO_MASTER_PYTHON set "EMO_MASTER_PYTHON=%USERPROFILE%\.conda\envs\emo_master\python.exe"
if not exist "%EMO_MASTER_PYTHON%" (
  echo Python not found: "%EMO_MASTER_PYTHON%"
  echo Set EMO_MASTER_PYTHON to the existing Python 3.10 environment's python.exe.
  exit /b 1
)
"%EMO_MASTER_PYTHON%" "%~dp0scripts\run_operator.py" %*
exit /b %ERRORLEVEL%
