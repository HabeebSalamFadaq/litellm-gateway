@echo off
REM Local dev starter - reads secrets from .env (or set them inline).
REM This file intentionally contains NO real keys. Put your keys in .env.
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set LITELLM_MASTER_KEY=sk-replace-me-with-something-long

REM If .env exists in the current folder, load its values.
if exist .env (
  for /f "usebackq tokens=1* delims==" %%a in (".env") do (
    set "line=%%a"
    if not "!line:~0,1!"=="#" set %%a=%%b
  )
)

cd /d %~dp0
"C:\Users\Zakrotix\AppData\Roaming\Python\Python314\Scripts\litellm.exe" --config config.yaml --port 4000 --host 0.0.0.0
