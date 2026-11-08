@echo off
chcp 936 >nul

rem ============================================================
rem  按端口加锁，防止同一个服务被启动两次
rem
rem  用法：
rem    call port_lock.bat acquire [端口]   拿到锁返回 0，被别人占着返回 1
rem    call port_lock.bat release [端口]   释放锁
rem  不写端口时默认 8000。
rem
rem  为什么需要它：
rem    服务从进程启动到真正 bind 端口有 2~3 秒空窗。在这段时间里
rem    连点两次启动脚本，两次的端口检查都会说"空闲"，于是都起来了。
rem      · 后端 --reload 模式底层设了 SO_REUSEADDR，两个都能 bind 成功
rem        → 「两个后端抢 8000，接口时好时坏，关一个另一个还占着」
rem      · 前端 vite 没设 strictPort，第二个会静默改用 5174，
rem        多留一个隐形进程
rem
rem    mkdir 是原子操作：两个进程同时执行只有一个成功。用它当锁，
rem    比"检查端口再启动"可靠得多。
rem ============================================================

set "ACTION=%~1"
set "PORT=%~2"
if "%PORT%"=="" set "PORT=8000"
set "LOCK=%TEMP%\jragent_port_%PORT%.lock"

if /i "%ACTION%"=="acquire" goto acquire
if /i "%ACTION%"=="release" goto release

echo 用法: call port_lock.bat acquire ^| release [端口]
exit /b 2


:acquire
mkdir "%LOCK%" 2>nul
if not errorlevel 1 exit /b 0

rem 锁已存在。先看是不是真有人在跑 —— 有人在跑就一定在监听端口。
netstat -ano | findstr ":%PORT%" | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 exit /b 1

rem 端口空闲但锁还在，两种可能：
rem   1) 另一个实例正在启动，还没 bind 端口
rem   2) 上次用 X 关了窗口 / 进程崩了，锁没来得及删
rem 等 5 秒再看：真在启动的话，这时候端口已经监听了。
echo 发现上次 %PORT% 端口留下的启动标记，等 5 秒确认…
ping -n 6 127.0.0.1 >nul

netstat -ano | findstr ":%PORT%" | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 exit /b 1

rem 确认没人在跑，清掉死锁重建
rmdir /s /q "%LOCK%" 2>nul
mkdir "%LOCK%" 2>nul
if errorlevel 1 exit /b 1
exit /b 0


:release
rmdir /s /q "%LOCK%" 2>nul
exit /b 0
