@echo off
cd /d "%~dp0"

if not exist "tools\cloudflared.exe" (
  echo cloudflared missing. Download tools\cloudflared.exe first.
  pause
  exit /b 1
)

echo [1/2] Starting AIVanya API on port 8001 ...
start "AIVanya-API" cmd /k "cd /d ""%~dp0"" && .\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8001 --reload"

timeout /t 5 /nobreak >nul

echo [2/2] Opening PUBLIC Cloudflare tunnel...
echo.
echo Browser me trycloudflare.com wala HTTPS URL dikhega — wahi public link hai.
echo PC band / ye window band = URL band.
echo.
tools\cloudflared.exe tunnel --url http://127.0.0.1:8001

pause
