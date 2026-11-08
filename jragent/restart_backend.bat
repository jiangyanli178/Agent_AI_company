@echo off
chcp 936 >nul
title 重启后端

cd /d "%~dp0backend"
set "PYTHONIOENCODING=gbk:replace"

echo ============================================
echo   重启后端：先清干净，再启动
echo ============================================
echo.

rem 要重启就先把这个锁放掉 —— 反正下面要把所有实例杀掉
call "%~dp0port_lock.bat" release

rem 杀两轮：--reload 的父进程不监听端口，第一轮按端口只杀掉 worker，
rem 父进程可能又把 worker 拉起来，所以必须来第二轮。
echo [1/3] 清理 8000 端口…
for /l %%i in (1,1,2) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
        echo    结束进程 %%a
        taskkill /F /T /PID %%a >nul 2>nul
    )
    ping -n 2 127.0.0.1 >nul
)

echo [2/3] 等端口释放…
ping -n 2 127.0.0.1 >nul

netstat -ano | findstr ":8000" | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 goto stillbusy

echo    端口空闲
echo.

if not exist ".venv\Scripts\uvicorn.exe" goto notfound

rem 杀完之后再抢锁：避免清理的同时脚本被点了第二次
call "%~dp0port_lock.bat" acquire
if errorlevel 1 goto lockfailed

echo [3/3] 启动后端（单进程模式）…
echo.
.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000
call "%~dp0port_lock.bat" release
goto done


:stillbusy
echo [警告] 8000 端口还是被占用，可能有别的程序占着。
echo.
netstat -ano | findstr ":8000" | findstr "LISTENING"
echo.
echo 上面最后那列是进程号，可以到任务管理器里结束它。
echo.
pause
exit /b 1


:lockfailed
echo [警告] 刚才有另一个启动脚本抢先了，这次不重复启动。
echo        稍等一下，或者双击 0_停止.bat 清干净再来。
echo.
pause
exit /b 1


:notfound
echo [ERROR] 找不到 backend\.venv\Scripts\uvicorn.exe
echo 先按 requirements.txt 建好虚拟环境。
echo.
pause
exit /b 1


:done
echo.
echo 后端已退出。按任意键关闭窗口。
pause
