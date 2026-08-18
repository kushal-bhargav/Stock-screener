@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_ROOT=%%~fI"
set "BACKEND_ROOT=%PROJECT_ROOT%\backend"
set "FRONTEND_ROOT=%PROJECT_ROOT%\frontend"
set "RUNTIME_ROOT=%PROJECT_ROOT%\.tmp-public-dev"
set "TOOLS_ROOT=%PROJECT_ROOT%\.tools"
set "PUBLIC_ENV=%FRONTEND_ROOT%\.env.public.local"

if "%~1"=="__run_backend" goto run_backend
if "%~1"=="__run_frontend" goto run_frontend
if "%~1"=="__run_tunnel" goto run_tunnel

set "BACKEND_PORT=%~1"
set "FRONTEND_PORT=%~2"
if "%BACKEND_PORT%"=="" set "BACKEND_PORT=8000"
if "%FRONTEND_PORT%"=="" set "FRONTEND_PORT=5173"

if not exist "%RUNTIME_ROOT%" mkdir "%RUNTIME_ROOT%"
if not exist "%TOOLS_ROOT%" mkdir "%TOOLS_ROOT%"

call :resolve_python
if errorlevel 1 exit /b 1

call :resolve_npm
if errorlevel 1 exit /b 1

call :resolve_cloudflared
if errorlevel 1 exit /b 1

echo Starting FastAPI on http://127.0.0.1:%BACKEND_PORT% ...
start "fmcg-backend" /B cmd /c ""%~f0" __run_backend"

call :wait_url "http://127.0.0.1:%BACKEND_PORT%/health" "FastAPI backend" 60
if errorlevel 1 goto fail

rem Same-origin tunnel pattern: expose only Vite; Vite proxies /api and /health to FastAPI.
(
    echo VITE_API_URL=
    echo VITE_API_PROXY_TARGET=http://127.0.0.1:%BACKEND_PORT%
) > "%PUBLIC_ENV%"

echo Starting Vite on http://127.0.0.1:%FRONTEND_PORT% ...
start "fmcg-frontend" /B cmd /c ""%~f0" __run_frontend"

call :wait_url "http://127.0.0.1:%FRONTEND_PORT%" "Vite frontend" 60
if errorlevel 1 goto fail

echo Creating public frontend tunnel with Cloudflare Quick Tunnel ...
start "fmcg-frontend-tunnel" /B cmd /c ""%~f0" __run_tunnel"

call :wait_tunnel 60
if errorlevel 1 goto fail
set "FRONTEND_PUBLIC_URL=!TUNNEL_URL!"

echo Waiting for public URL readiness ...
call :wait_url "!FRONTEND_PUBLIC_URL!" "Cloudflare frontend URL" 90
if errorlevel 1 (
    echo URL was created but did not become reachable yet. Try opening it after a short wait:
    echo   !FRONTEND_PUBLIC_URL!
) else (
    echo Public URL is reachable.
)

echo.
echo Public dev endpoint is ready:
echo   Frontend: !FRONTEND_PUBLIC_URL!
echo.
echo Backend is not separately public. The public frontend proxies /api and /health to:
echo   http://127.0.0.1:%BACKEND_PORT%
echo.
echo Keep this Command Prompt window open. Press Ctrl+C to stop watching.
echo Logs are in "%RUNTIME_ROOT%"
echo.

:watch
timeout /t 2 /nobreak >nul
goto watch

:fail
echo.
echo Public dev startup failed. Logs are in "%RUNTIME_ROOT%".
echo To stop leftover processes, close this window or run:
echo   taskkill /f /im cloudflared.exe
echo   taskkill /f /im node.exe
echo   taskkill /f /im python.exe
exit /b 1

:run_backend
cd /d "%BACKEND_ROOT%" || exit /b 1
"%PYTHON_EXE%" -m uvicorn api:app --host 0.0.0.0 --port %BACKEND_PORT% --reload > "%RUNTIME_ROOT%\backend.out.log" 2> "%RUNTIME_ROOT%\backend.err.log"
exit /b %ERRORLEVEL%

:run_frontend
cd /d "%FRONTEND_ROOT%" || exit /b 1
%NPM% run dev -- --host 0.0.0.0 --port %FRONTEND_PORT% --mode public > "%RUNTIME_ROOT%\frontend.out.log" 2> "%RUNTIME_ROOT%\frontend.err.log"
exit /b %ERRORLEVEL%

:run_tunnel
"%CLOUDFLARED_EXE%" tunnel --url http://127.0.0.1:%FRONTEND_PORT% --no-autoupdate > "%RUNTIME_ROOT%\cloudflared-frontend.out.log" 2> "%RUNTIME_ROOT%\cloudflared-frontend.err.log"
exit /b %ERRORLEVEL%

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

:resolve_npm
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
exit /b 0

:resolve_cloudflared
set "CLOUDFLARED_EXE="
where cloudflared >nul 2>nul
if not errorlevel 1 (
    set "CLOUDFLARED_EXE=cloudflared"
    exit /b 0
)

set "LOCAL_CLOUDFLARED=%TOOLS_ROOT%\cloudflared.exe"
if exist "%LOCAL_CLOUDFLARED%" (
    set "CLOUDFLARED_EXE=%LOCAL_CLOUDFLARED%"
    exit /b 0
)

echo cloudflared was not found on PATH.
echo Downloading Cloudflare Tunnel locally. No login or token is needed for Quick Tunnel...
curl.exe -L --fail --output "%LOCAL_CLOUDFLARED%" "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
if errorlevel 1 (
    echo Failed to download cloudflared.exe.
    echo You can manually download it to:
    echo   "%LOCAL_CLOUDFLARED%"
    exit /b 1
)

set "CLOUDFLARED_EXE=%LOCAL_CLOUDFLARED%"
exit /b 0

:wait_url
set "WAIT_URL=%~1"
set "WAIT_NAME=%~2"
set /a "WAIT_LIMIT=%~3"
set /a "WAIT_COUNT=0"
:wait_url_loop
curl.exe -fsS "%WAIT_URL%" >nul 2>nul
if not errorlevel 1 exit /b 0
if %WAIT_COUNT% GEQ %WAIT_LIMIT% (
    echo %WAIT_NAME% did not respond at %WAIT_URL% within %WAIT_LIMIT% seconds.
    exit /b 1
)
set /a "WAIT_COUNT+=1"
timeout /t 1 /nobreak >nul
goto wait_url_loop

:wait_tunnel
set /a "TUNNEL_LIMIT=%~1"
set /a "TUNNEL_COUNT=0"
set "TUNNEL_URL="
set "TUNNEL_OUT=%RUNTIME_ROOT%\cloudflared-frontend.out.log"
set "TUNNEL_ERR=%RUNTIME_ROOT%\cloudflared-frontend.err.log"
:wait_tunnel_loop
for %%L in ("%TUNNEL_OUT%" "%TUNNEL_ERR%") do (
    if exist "%%~L" (
        for /f "tokens=* delims=" %%U in ('findstr /r /c:"https://[-a-zA-Z0-9.]*trycloudflare\.com" "%%~L"') do (
            for %%W in (%%U) do (
                echo %%W | findstr /r "^https://[-a-zA-Z0-9.]*trycloudflare\.com" >nul
                if not errorlevel 1 (
                    set "TUNNEL_URL=%%W"
                    exit /b 0
                )
            )
        )
    )
)
if %TUNNEL_COUNT% GEQ %TUNNEL_LIMIT% (
    echo Timed out waiting for the Cloudflare frontend tunnel URL.
    exit /b 1
)
set /a "TUNNEL_COUNT+=1"
timeout /t 1 /nobreak >nul
goto wait_tunnel_loop
