@echo off
chcp 936 >nul
title 全域策略研究所 - 前端 5173

cd /d "%~dp0frontend"

echo ============================================
echo   全域策略研究所 - 前端
echo   http://localhost:5173
echo ============================================
echo.

rem 先抢锁再查端口。只查端口是不够的：vite 从启动到 bind 有 1~2 秒空窗，
rem 这期间连点两次会同时通过检查；而且 vite 没设 strictPort，
rem 第二个会静默改用 5174 —— 你会在 5173 上看到正常的页面，
rem 但任务管理器里多了个没人管的 node 进程。
call "%~dp0port_lock.bat" acquire 5173
if errorlevel 1 goto portbusy

netstat -ano | findstr ":5173" | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 goto portbusy_release

echo 提示：窗口里不要用鼠标点选文本，会进入"选择"模式并暂停进程。
echo       万一点了，按 Esc 退出。要停止服务请双击 0_停止.bat。
echo.

where node >nul 2>nul
if not errorlevel 1 goto usesystem

set "NODE_HOME=C:\Users\lu\.workbuddy\binaries\node\versions\22.22.2-3"
if exist "%NODE_HOME%\node.exe" goto usebundled
goto nonode


:portbusy_release
call "%~dp0port_lock.bat" release 5173

:portbusy
echo [警告] 5173 端口已经有一个前端在跑了，这次不启动。
echo.
echo   再起一个的话，vite 会悄悄改用 5174，
echo   你会多一个看不见的 node 进程，而且浏览器打开的还是 5173。
echo.
echo   要停止现在这个：双击 0_停止.bat。
echo.
pause
exit /b 1


:usesystem
npm run dev
goto done


:usebundled
echo 使用内置 node 启动
echo.
REM 必须把 node 目录加进 PATH：
REM npm 执行 vite 时会再调一次 node，PATH 里没有就会报 "'node'" 不是内部或外部命令
set "PATH=%NODE_HOME%;%PATH%"
"%NODE_HOME%\node.exe" "%NODE_HOME%\node_modules\npm\bin\npm-cli.js" run dev
goto done


:nonode
echo [ERROR] 找不到 node，请先安装 Node.js 20 以上。


:done
call "%~dp0port_lock.bat" release 5173
echo.
echo 前端已退出。按任意键关闭窗口。
pause
