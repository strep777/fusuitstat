@echo off
rem ===========================================================================
rem  Starium Studio website - local dev launcher for Windows
rem
rem  ASCII ONLY. cmd.exe parses batch files with the active code page; a
rem  UTF-8 .bat read under code page 936 drifts off the line boundaries and
rem  executes comment text as commands. Every byte below stays under 0x80.
rem
rem  Usage:
rem    start.bat                start backend :8000 and frontend :5173
rem    start.bat --no-browser   start without opening the browser
rem    start.bat --install      reinstall dependencies before starting
rem    start.bat --no-reload    start backend without hot reload
rem    start.bat --help         show help
rem    start.bat stop           stop the dev processes on 8000 / 5173
rem
rem  Ports are fixed on purpose:
rem    web/vite.config.ts proxies /api to 127.0.0.1:8000;
rem    the dev CORS allowlist only accepts 127.0.0.1:5173 and localhost:5173.
rem
rem  Linux / macOS / Git Bash: use start.sh instead.
rem ===========================================================================

setlocal EnableExtensions
cd /d "%~dp0"

set "ROOT=%CD%"
set "SERVER_DIR=%ROOT%\server"
set "WEB_DIR=%ROOT%\web"
set "LOG_DIR=%ROOT%\.logs"
set "PY=%SERVER_DIR%\.venv\Scripts\python.exe"
set "DB_FILE=%SERVER_DIR%\var\starium.db"

set "API_PORT=8000"
set "WEB_PORT=5173"
set "API_URL=http://127.0.0.1:%API_PORT%"
set "WEB_URL=http://127.0.0.1:%WEB_PORT%/"
set "ADMIN_URL=http://127.0.0.1:%WEB_PORT%/admin.html"

set "OPEN_BROWSER=1"
set "FORCE_INSTALL=0"
set "ACTION=start"
set "RELOAD_ARG=--reload"
set "BADARG=0"
set "FOUND=0"
set "API_REUSED=0"
set "WEB_REUSED=0"
set "PAUSE_DONE=0"

rem  Readiness timeout in seconds. Slow machine: set STARIUM_WAIT=300 first.
if not defined STARIUM_WAIT set "STARIUM_WAIT=120"
if not defined UV_CACHE_DIR set "UV_CACHE_DIR=%ROOT%\.uv-cache"

rem  Double-click (Explorer) keeps the window open long enough to read output.
if not defined STARIUM_PAUSE (
    echo %CMDCMDLINE% | findstr /I /C:"/c " >nul && set "STARIUM_PAUSE=1"
)

rem ---------------------------------------------------------------------------
rem  Arguments
rem ---------------------------------------------------------------------------
:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="stop" (
    set "ACTION=stop"
    shift
    goto :parse_args
)
if /i "%~1"=="--no-browser" (
    set "OPEN_BROWSER=0"
    shift
    goto :parse_args
)
if /i "%~1"=="--no-open" (
    set "OPEN_BROWSER=0"
    shift
    goto :parse_args
)
if /i "%~1"=="--install" (
    set "FORCE_INSTALL=1"
    shift
    goto :parse_args
)
if /i "%~1"=="--no-reload" (
    set "RELOAD_ARG="
    shift
    goto :parse_args
)
if /i "%~1"=="-h" goto :usage
if /i "%~1"=="--help" goto :usage
echo [error] Unknown option: %~1
echo.
set "BADARG=1"
goto :usage

:args_done
if not exist "%SERVER_DIR%\app\main.py" (
    echo [error] server\app\main.py not found.
    echo         Put start.bat in the repository root, next to server\ and web\.
    goto :fail_exit
)
if /i "%ACTION%"=="stop" goto :do_stop

echo.
echo ============================================================
echo   Starium Studio - local dev environment
echo ============================================================
echo.

call :check_env
if errorlevel 1 goto :fail
call :ensure_backend
if errorlevel 1 goto :fail
call :ensure_database
if errorlevel 1 goto :fail
call :ensure_web
if errorlevel 1 goto :fail
call :start_api
if errorlevel 1 goto :fail
call :start_web
if errorlevel 1 goto :fail
call :success
exit /b 0

rem ---------------------------------------------------------------------------
rem  Environment and dependencies
rem ---------------------------------------------------------------------------
:check_env
where node >nul 2>nul
if errorlevel 1 (
    echo [error] Node.js not found. Install Node.js 20.19 or newer.
    exit /b 1
)
where pnpm >nul 2>nul
if errorlevel 1 (
    echo [error] pnpm not found. Run: npm i -g pnpm
    exit /b 1
)
echo [ok] Node.js and pnpm are available
exit /b 0

:ensure_backend
if "%FORCE_INSTALL%"=="0" if exist "%PY%" (
    echo [ok] Backend virtualenv found: server\.venv
    exit /b 0
)
where uv >nul 2>nul
if errorlevel 1 (
    echo [error] uv not found. Install uv first: https://docs.astral.sh/uv/
    exit /b 1
)
echo [1/3] Installing backend dependencies: uv sync
pushd "%SERVER_DIR%"
uv sync
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" (
    echo [error] uv sync failed. Check network access and uv configuration.
    exit /b 1
)
if not exist "%PY%" (
    echo [error] uv sync finished but %PY% is still missing.
    exit /b 1
)
echo [ok] Backend dependencies ready
exit /b 0

:ensure_database
if exist "%DB_FILE%" (
    echo [ok] Database found: server\var\starium.db
    exit /b 0
)
echo [2/3] First run: creating the database with alembic upgrade head
pushd "%SERVER_DIR%"
"%PY%" -m app.cli init-db
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" (
    echo [error] Database initialisation failed, see the log above.
    exit /b 1
)
echo [2/3] Seeding demo data, this step is idempotent
pushd "%SERVER_DIR%"
"%PY%" -m app.cli seed-demo
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" (
    echo [hint] seed-demo failed. Pages may miss demo data; the site still starts.
)
echo [ok] Database ready
exit /b 0

:ensure_web
if "%FORCE_INSTALL%"=="0" if exist "%WEB_DIR%\node_modules\vite\bin\vite.js" (
    echo [ok] Frontend dependencies found: web\node_modules
    exit /b 0
)
echo [3/3] Installing frontend dependencies: pnpm install
pushd "%WEB_DIR%"
if exist pnpm-lock.yaml goto :web_install_frozen
call pnpm install
goto :web_install_done
:web_install_frozen
call pnpm install --frozen-lockfile
if errorlevel 1 (
    echo [hint] frozen lockfile install failed, retrying with a fresh resolve
    call pnpm install
)
:web_install_done
set "RC=%ERRORLEVEL%"
popd
if not "%RC%"=="0" (
    echo [error] pnpm install failed. Check network access and the lockfile.
    exit /b 1
)
echo [ok] Frontend dependencies ready
exit /b 0

rem ---------------------------------------------------------------------------
rem  Backend
rem ---------------------------------------------------------------------------
:start_api
call :port_in_use %API_PORT%
if errorlevel 1 goto :start_api_launch
call :api_ready
if errorlevel 1 (
    echo [error] Port %API_PORT% is in use but /readyz does not answer.
    echo         Run: start.bat stop
    exit /b 1
)
echo [hint] Port %API_PORT% already serves this project, reusing it.
set "API_REUSED=1"
exit /b 0

:start_api_launch
echo [start] Backend http://127.0.0.1:%API_PORT%
start "starium api :%API_PORT%" /D "%SERVER_DIR%" cmd /k ""%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %API_PORT% %RELOAD_ARG%"
call :wait_http "%API_URL%/readyz" "Backend"
if errorlevel 1 (
    echo [error] Backend did not become ready. Check the backend window.
    exit /b 1
)
echo [ok] Backend ready: %API_URL%/api/docs
exit /b 0

rem ---------------------------------------------------------------------------
rem  Frontend
rem ---------------------------------------------------------------------------
:start_web
call :port_in_use %WEB_PORT%
if errorlevel 1 goto :start_web_launch
call :web_ready
if errorlevel 1 (
    echo [error] Port %WEB_PORT% is in use by another program.
    echo         Run: start.bat stop
    exit /b 1
)
echo [hint] Port %WEB_PORT% already serves this project, reusing it.
set "WEB_REUSED=1"
exit /b 0

:start_web_launch
echo [start] Frontend http://127.0.0.1:%WEB_PORT%
start "starium web :%WEB_PORT%" /D "%WEB_DIR%" cmd /k "node node_modules\vite\bin\vite.js --host 127.0.0.1 --port %WEB_PORT% --strictPort"
call :wait_http "%WEB_URL%" "Frontend"
if errorlevel 1 (
    echo [error] Frontend did not become ready. Check the frontend window.
    exit /b 1
)
echo [ok] Frontend ready
exit /b 0

:success
echo.
echo ------------------------------------------------------------
echo   Home page    %WEB_URL%
echo   Admin        %ADMIN_URL%
echo   API docs     %API_URL%/api/docs
echo   Health check %API_URL%/healthz
echo.
echo   Stop         start.bat stop   or close the two server windows
echo ------------------------------------------------------------
echo.
if "%OPEN_BROWSER%"=="1" (
    echo [open] %WEB_URL%
    start "" "%WEB_URL%"
)
call :pause_if_double_click 30
exit /b 0

rem ---------------------------------------------------------------------------
rem  Stop
rem ---------------------------------------------------------------------------
:do_stop
echo.
call :kill_port %WEB_PORT%
call :kill_port %API_PORT%
echo.
if "%FOUND%"=="0" (
    echo [ok] Nothing is listening on %WEB_PORT% / %API_PORT%.
) else (
    echo [ok] Dev processes stopped.
)
echo.
call :pause_if_double_click 10
exit /b 0

:kill_port
for /f "tokens=5" %%p in ('netstat -ano -p tcp ^| findstr /R /C:":%~1 .*LISTENING"') do (
    set "FOUND=1"
    echo [stop] port %~1 - PID %%p
    taskkill /F /T /PID %%p >nul 2>nul
)
exit /b 0

rem ---------------------------------------------------------------------------
rem  Failures
rem ---------------------------------------------------------------------------
:fail
echo.
echo [failed] Startup did not finish, cleaning up processes started by this run.
call :cleanup_started
echo Fix the message above and run start.bat again.
call :pause_if_double_click 60
exit /b 1

:fail_exit
call :pause_if_double_click 60
exit /b 1

:cleanup_started
if not "%API_REUSED%"=="1" call :kill_port %API_PORT%
if not "%WEB_REUSED%"=="1" call :kill_port %WEB_PORT%
exit /b 0

rem ---------------------------------------------------------------------------
rem  Helpers
rem ---------------------------------------------------------------------------

rem  Port has a listener: exit 0
:port_in_use
netstat -ano -p tcp | findstr /R /C:":%~1 .*LISTENING" >nul
exit /b %ERRORLEVEL%

rem  Backend /readyz answers: exit 0
:api_ready
call :http_ok "%API_URL%/readyz"
exit /b %ERRORLEVEL%

rem  HTTP GET returns 2xx/3xx: exit 0
:http_ok
curl.exe -fsS --max-time 3 -o nul "%~1" >nul 2>nul
if not errorlevel 1 exit /b 0
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 '%~1'; if ($r.StatusCode -lt 400) { exit 0 } } catch { }; exit 1" >nul 2>nul
if errorlevel 1 exit /b 1
exit /b 0

rem  Port serves this project's frontend (home page mentions site-main.ts): exit 0
:web_ready
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $c = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 '%WEB_URL%').Content; if ($c -like '*site-main.ts*') { exit 0 } } catch { }; exit 1" >nul 2>nul
if errorlevel 1 exit /b 1
exit /b 0

rem  %~1 = URL, %~2 = display name; waits up to STARIUM_WAIT seconds
:wait_http
echo [wait] %~2 ready, up to %STARIUM_WAIT% seconds...
set /a WAITED=0
:wait_http_loop
call :http_ok "%~1"
if not errorlevel 1 exit /b 0
set /a WAITED+=1
if %WAITED% GEQ %STARIUM_WAIT% (
    echo [error] %~2 was not ready after %STARIUM_WAIT% seconds.
    exit /b 1
)
ping -n 2 127.0.0.1 >nul 2>nul
goto :wait_http_loop

rem  %~1 = seconds; only pauses when the window was opened by a double-click
:pause_if_double_click
if not "%STARIUM_PAUSE%"=="1" exit /b 0
if "%PAUSE_DONE%"=="1" exit /b 0
set "PAUSE_DONE=1"
echo Window closes in %~1 seconds. Press Ctrl+C to keep it open.
timeout /t %~1 >nul 2>nul
exit /b 0

rem ---------------------------------------------------------------------------
rem  Usage
rem ---------------------------------------------------------------------------
:usage
echo.
echo Usage: start.bat [option]
echo.
echo   start.bat                start backend :8000 and frontend :5173
echo   start.bat --no-browser   start without opening the browser
echo   start.bat --install      reinstall dependencies before starting
echo   start.bat --no-reload    start backend without hot reload
echo   start.bat --help         show this help
echo   start.bat stop           stop the dev processes on 8000 / 5173
echo.
echo Environment: STARIUM_WAIT=seconds sets the readiness timeout, default 120.
echo.
call :pause_if_double_click 20
if "%BADARG%"=="1" exit /b 1
exit /b 0
