@echo off
chcp 936 >nul
title 全域策略研究所 - 后端 8000（开发模式）

cd /d "%~dp0backend"
set "PYTHONIOENCODING=gbk:replace"

echo ============================================
echo   全域策略研究所 - 后端（开发模式）
echo   http://127.0.0.1:8000/docs
echo ============================================
echo.
echo 这个模式会监听代码变化并自动重启，方便改后端。
echo 代价是 uvicorn 会跑「父进程 + 子进程」两个 python，
echo 关窗口时偶尔留残留 —— 真遇到了就双击 0_停止.bat。
echo.
echo 平时用不着自动重载的话，请用 start_backend.bat（单进程，干净）。
echo.

rem 开发模式比单进程更需要这把锁：
rem --reload 底层设了 SO_REUSEADDR，Windows 允许两个实例都 bind 到 8000，
rem 而且它们不会报错 —— 只靠端口检查根本拦不住。
call "%~dp0port_lock.bat" acquire
if errorlevel 1 goto portbusy

netstat -ano | findstr ":8000" | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 goto portbusy_release

if exist ".venv\Scripts\uvicorn.exe" goto usevenv
goto notfound


:portbusy_release
call "%~dp0port_lock.bat" release

:portbusy
echo [警告] 8000 端口已经有一个后端在跑了，这次不启动。
echo.
echo   开发模式用 --reload，它和另一个实例能同时 bind 同一个端口，
echo   两个都"看起来正常"，请求却随机落到其中一个 —— 这就是卡顿的来源。
echo.
echo   要重启用 restart_backend.bat，要停止用 0_停止.bat。
echo.
pause
exit /b 1


:usevenv
echo 启动中（--reload）…
echo.
.venv\Scripts\uvicorn.exe app.main:app --reload --host 127.0.0.1 --port 8000
if errorlevel 1 goto runfailed
goto done


:runfailed
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 goto bindlost
echo.
echo [ERROR] 后端异常退出，具体原因看上面几行输出。
goto done

:bindlost
echo.
echo [提示] 启动过程中 8000 被别人抢走了，一般是在两个窗口里同时点了启动。
echo        先双击 0_停止.bat 清干净，再只启动一个。
goto done


:notfound
echo [ERROR] 找不到 backend\.venv\Scripts\uvicorn.exe
echo 先按 requirements.txt 建好虚拟环境。


:done
call "%~dp0port_lock.bat" release
echo.
echo 后端已退出。按任意键关闭窗口。
pause
