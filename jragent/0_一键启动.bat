@echo off
chcp 936 >nul
title 一键启动 - 全域策略研究所

rem ============================================================
rem  这是唯一需要双击的文件。
rem
rem  它会：后端没跑就开一个后端窗口，前端没跑就开一个前端窗口，
rem        等前端就绪后自动打开浏览器。
rem  已经全在跑的时候再双击它，只会打开浏览器，不会再起一份。
rem
rem  想跳过自动开浏览器（自动化测试用）：先 set JR_NO_BROWSER=1
rem ============================================================

set "ROOT=%~dp0"

echo ============================================
echo   一键启动（后端 8000 + 前端 5173）
echo ============================================
echo.

set "BE="
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 set "BE=1"

set "FE="
netstat -ano | findstr ":5173" | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 set "FE=1"

if defined BE if defined FE goto already

if defined BE goto skipbe
echo [1/2] 打开后端窗口…
start "后端 8000" cmd /c ""%ROOT%start_backend.bat""
goto checkfe
:skipbe
echo [1/2] 后端已经在跑（8000 在监听），跳过

:checkfe
if defined FE goto skipfe
echo [2/2] 打开前端窗口…
start "前端 5173" cmd /c ""%ROOT%start_frontend.bat""
goto waitready
:skipfe
echo [2/2] 前端已经在跑（5173 在监听），跳过


:waitready
echo.
echo 等前端起来（最多等 40 秒）…
set /a N=0

:loop
ping -n 2 127.0.0.1 >nul
set /a N+=1
netstat -ano | findstr ":5173" | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 goto ready
if %N% GEQ 40 goto slowstart
goto loop

:ready
echo.
echo 启动了，正在打开浏览器：http://localhost:5173
if defined JR_NO_BROWSER goto end
start "" http://localhost:5173
goto end

:slowstart
echo.
echo [提示] 前端 40 秒还没起来。看一下「前端 5173」那个窗口，
echo        里面有报错信息（最常见是 node 找不到，或者 5173 被别的程序占了）。

:end
echo.
echo --------------------------------------------
echo  · 本窗口只是个启动器，关掉它不会停服务
echo  · 要停服务：双击 0_停止.bat
echo  · 已经在跑的时候再双击本文件，只会打开浏览器
echo --------------------------------------------
pause
exit /b 0


:already
echo 后端和前端都已经在跑了，直接打开浏览器。
if defined JR_NO_BROWSER goto alreadyend
start "" http://localhost:5173
:alreadyend
echo.
echo   想重启后端：restart_backend.bat
echo   想全部停掉：0_停止.bat
echo.
pause
exit /b 0
