@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_ROOT=%%~fI"
set "BACKEND_ROOT=%PROJECT_ROOT%\backend"
set "FRONTEND_ROOT=%PROJECT_ROOT%\frontend"
set "RUNTIME_ROOT=%PROJECT_ROOT%\.tmp-public-dev"
set "PUBLIC_ENV=%FRONTEND_ROOT%\.env.public.local"

if "%~1"=="__run_backend" goto run_backend
if "%~1"=="__run_frontend" goto run_frontend
if "%~1"=="__run_tunnel" (
    set "TUNNEL_CHILD_NAME=%~2"
    set "TUNNEL_CHILD_URL=%~3"
    goto run_tunnel
)

set "BACKEND_PORT=%~1"
set "FRONTEND_PORT=%~2"
if "%BACKEND_PORT%"=="" set "BACKEND_PORT=8000"
if "%FRONTEND_PORT%"=="" set "FRONTEND_PORT=5173"

if not exist "%RUNTIME_ROOT%" mkdir "%RUNTIME_ROOT%"

call :resolve_python
if errorlevel 1 exit /b 1

where npm.cmd >nul 2>nul
if errorlevel 1 (
    where npm >nul 2>nul
    if errorlevel 1 (
        echo npm was not found on PATH.
        exit /b 1
    )
    set "NPM=npm"
) else (
    set "NPM=npm.cmd"
)

where cloudflared >nul 2>nul
if errorlevel 1 (
    echo cloudflared was not found on PATH.
    echo Install Cloudflare Tunnel, then rerun this script. Quick Tunnel does not need a token.
    exit /b 1
)

echo Starting FastAPI on http://127.0.0.1:%BACKEND_PORT% ...
start "fmcg-backend" /B cmd /c ""%~f0" __run_backend"

call :wait_url "http://127.0.0.1:%BACKEND_PORT%/health" "FastAPI backend" 60
if errorlevel 1 goto fail

echo Creating public backend tunnel ...
start "fmcg-backend-tunnel" /B cmd /c ""%~f0" __run_tunnel backend http://127.0.0.1:%BACKEND_PORT%"

call :wait_tunnel "backend" 45
if errorlevel 1 goto fail
set "BACKEND_PUBLIC_URL=!TUNNEL_URL!"

(
    echo VITE_API_URL=!BACKEND_PUBLIC_URL!
    echo VITE_API_PROXY_TARGET=http://127.0.0.1:%BACKEND_PORT%
) > "%PUBLIC_ENV%"

echo Starting Vite on http://127.0.0.1:%FRONTEND_PORT% ...
start "fmcg-frontend" /B cmd /c ""%~f0" __run_frontend"

call :wait_url "http://127.0.0.1:%FRONTEND_PORT%" "Vite frontend" 60
if errorlevel 1 goto fail

echo Creating public frontend tunnel ...
start "fmcg-frontend-tunnel" /B cmd /c ""%~f0" __run_tunnel frontend http://127.0.0.1:%FRONTEND_PORT%"

call :wait_tunnel "frontend" 45
if errorlevel 1 goto fail
set "FRONTEND_PUBLIC_URL=!TUNNEL_URL!"

echo.
echo Public dev endpoints are ready:
echo   Frontend: !FRONTEND_PUBLIC_URL!
echo   Backend:  !BACKEND_PUBLIC_URL!
echo.
echo Keep this Command Prompt window open. Press Ctrl+C to stop watching.
echo Logs are in "%RUNTIME_ROOT%"
echo.

:watch
timeout /t 2 /nobreak >nul
goto watch

:run_backend
cd /d "%BACKEND_ROOT%" || exit /b 1
"%PYTHON_EXE%" -m uvicorn api:app --host 0.0.0.0 --port %BACKEND_PORT% --reload > "%RUNTIME_ROOT%\backend.out.log" 2> "%RUNTIME_ROOT%\backend.err.log"
exit /b %ERRORLEVEL%

:run_frontend
cd /d "%FRONTEND_ROOT%" || exit /b 1
%NPM% run dev -- --host 0.0.0.0 --port %FRONTEND_PORT% --mode public > "%RUNTIME_ROOT%\frontend.out.log" 2> "%RUNTIME_ROOT%\frontend.err.log"
exit /b %ERRORLEVEL%

:run_tunnel
cloudflared tunnel --url %TUNNEL_CHILD_URL% --no-autoupdate > "%RUNTIME_ROOT%\cloudflared-%TUNNEL_CHILD_NAME%.out.log" 2> "%RUNTIME_ROOT%\cloudflared-%TUNNEL_CHILD_NAME%.err.log"
exit /b %ERRORLEVEL%

:fail
echo.
echo Public dev startup failed. Logs are in "%RUNTIME_ROOT%".
echo To stop leftover processes, close this window or run:
echo   taskkill /f /im cloudflared.exe
echo   taskkill /f /im node.exe
echo   taskkill /f /im python.exe
exit /b 1

:resolve_python
set "PYTHON_EXE="
if exist "%PROJECT_ROOT%\..\venv\Scripts\python.exe" set "PYTHON_EXE=%PROJECT_ROOT%\..\venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%PROJECT_ROOT%\venv\Scripts\python.exe" set "PYTHON_EXE=%PROJECT_ROOT%\venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%BACKEND_ROOT%\.venv\Scripts\python.exe" set "PYTHON_EXE=%BACKEND_ROOT%\.venv\Scripts\python.exe"
if not defined PYTHON_EXE (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python was not found. Activate your virtualenv or install Python.
        exit /b 1
    )
    set "PYTHON_EXE=python"
)
exit /b 0

:wait_url
set "WAIT_URL=%~1"
set "WAIT_NAME=%~2"
set /a "WAIT_LIMIT=%~3"
set /a "WAIT_COUNT=0"
:wait_url_loop
curl -fsS "%WAIT_URL%" >nul 2>nul
if not errorlevel 1 exit /b 0
if %WAIT_COUNT% GEQ %WAIT_LIMIT% (
    echo %WAIT_NAME% did not respond at %WAIT_URL% within %WAIT_LIMIT% seconds.
    exit /b 1
)
set /a "WAIT_COUNT+=1"
timeout /t 1 /nobreak >nul
goto wait_url_loop

:wait_tunnel
set "TUNNEL_NAME=%~1"
set /a "TUNNEL_LIMIT=%~2"
set /a "TUNNEL_COUNT=0"
set "TUNNEL_URL="
set "TUNNEL_OUT=%RUNTIME_ROOT%\cloudflared-%TUNNEL_NAME%.out.log"
set "TUNNEL_ERR=%RUNTIME_ROOT%\cloudflared-%TUNNEL_NAME%.err.log"
:wait_tunnel_loop
for %%L in ("%TUNNEL_OUT%" "%TUNNEL_ERR%") do (
    if exist "%%~L" (
        for /f "tokens=* delims=" %%U in ('findstr /r /c:"https://[-a-zA-Z0-9]*\.trycloudflare\.com" "%%~L"') do (
            for %%W in (%%U) do (
                echo %%W | findstr /r "^https://[-a-zA-Z0-9]*\.trycloudflare\.com" >nul
                if not errorlevel 1 (
                    set "TUNNEL_URL=%%W"
                    exit /b 0
                )
            )
        )
    )
)
if %TUNNEL_COUNT% GEQ %TUNNEL_LIMIT% (
    echo Timed out waiting for the Cloudflare %TUNNEL_NAME% tunnel URL.
    exit /b 1
)
set /a "TUNNEL_COUNT+=1"
timeout /t 1 /nobreak >nul
goto wait_tunnel_loop
