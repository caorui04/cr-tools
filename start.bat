@echo off
rem cr-tools launcher from source (ASCII only)
setlocal
set "ROOT=%~dp0"
set "DSH_HOME=%ROOT%.dsh-home"
set "DSH_NPX=%ROOT%node_modules"
set "PY=%ROOT%pipeline\.venv\Scripts\python.exe"
set "KB_TOOLS_DIR=%ROOT%tools"
cd /d "%ROOT%pipeline"

rem --- model presence hint ---
if not exist "%ROOT%tools\models\bge-m3-Q8_0.gguf" (
  echo.
  echo [hint] models not found. Run:
  echo   powershell -ExecutionPolicy Bypass -File scripts\download-models.ps1
  echo   or follow docs\INSTALL.md (manual download).
  echo.
)

rem 1) pipeline service (port 8737)
start "cr-pipeline" /min cmd /c ""%PY%" -m server.main"

rem 2) embedding BGE-M3 (port 8083) - if present
if exist "%ROOT%tools\llama\llama-server.exe" (
  start "cr-embedding" /min cmd /c ""%ROOT%tools\llama\llama-server.exe" --model "%ROOT%tools\models\bge-m3-Q8_0.gguf" --port 8083 --host 127.0.0.1 -ngl 99 --embeddings -b 4096 -ub 4096"
)

rem 3) DSH web UI (port 3180) - wait for pipeline then launch
timeout /t 6 /nobreak >nul
"%DSH_NPX%\.bin\dsh.cmd" --profile web --port 3180
endlocal
