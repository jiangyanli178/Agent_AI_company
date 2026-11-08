@echo off
chcp 936 >nul
title 全域策略研究所 - 后端 8000

cd /d "%~dp0backend"

rem 让 Python 的输出按 GBK 写，和 chcp 936 保持一致。
rem 否则 uvicorn 抛中文报错时会变成一串乱码（以前见过一次）。
set "PYTHONIOENCODING=gbk:replace"

echo ============================================
echo   全域策略研究所 - 后端
echo   http://127.0.0.1:8000/docs
echo ============================================
echo.

rem 抢启动锁放在最前面。不能只靠"检查端口再启动" ——
rem uvicorn 从启动到 bind 有 2~3 秒空窗，这期间连点两次会同时通过检查，
rem 结果就是两个后端抢 8000：接口时好时坏、关一个另一个还占着。
rem mkdir 是原子操作，同时只有一个能成功。
call "%~dp0port_lock.bat" acquire
if errorlevel 1 goto portbusy

rem 拿到锁了还要再查一次端口：可能有人绕过这个脚本直接跑 uvicorn
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 goto portbusy_release

if exist ".venv\Scripts\uvicorn.exe" goto usevenv
where uvicorn >nul 2>nul
if not errorlevel 1 goto usesystem
goto notfound


:portbusy_release
call "%~dp0port_lock.bat" release

:portbusy
echo [警告] 8000 端口已经有一个后端在跑了，这次不启动。
echo.
echo   现在再起一个，两个进程会抢同一个端口，
echo   表现就是接口时好时坏、卡住、而且关掉一个另一个还占着。
echo.
echo   如果确实要重启：双击 restart_backend.bat（它自己会先清干净）。
echo   只想停掉现在这个：双击 0_停止.bat。
echo.
pause
exit /b 1


:usevenv
echo 使用 backend\.venv 启动（单进程模式）
echo.
echo 提示：
echo   · 这个窗口不要用鼠标点选文本，会进入"选择"模式并暂停进程
echo     万一点了，按 Esc 退出，X 就恢复正常
echo   · 要停止服务：直接关这个窗口，或者双击 0_停止.bat
echo   · 改了后端代码需要重启才生效（想自动重载用 start_backend_dev.bat）
echo.
.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000
if errorlevel 1 goto runfailed
goto done


:usesystem
echo 使用系统 uvicorn 启动（单进程模式）
echo.
uvicorn app.main:app --host 127.0.0.1 --port 8000
if errorlevel 1 goto runfailed
goto done


:runfailed
rem uvicorn 非正常退出。先看端口是不是已经被别人拿着，
rem 这是唯一一种"启动过程中被抢"的情况，单独给个看得懂的提示。
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 goto bindlost
echo.
echo [ERROR] 后端异常退出，具体原因看上面几行输出。
echo         常见原因：数据库没起来、代码里有语法错误。
goto done

:bindlost
echo.
echo [提示] 启动过程中 8000 被别人抢走了 —— 一般是连点了两次启动脚本。
echo        现在跑着的那个实例是正常的，接口能用，不用管它。
echo        想确认只剩一份：双击 0_停止.bat 看端口，再重新启动。
goto done


:notfound
echo.
echo [ERROR] 找不到可用的 uvicorn。
echo.
echo 先建项目虚拟环境：
echo     cd backend
echo     python -m venv .venv
echo     .venv\Scripts\activate
echo     pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
echo.
echo 注意：不要用 Anaconda 的 Python 3.8，它的 fastapi 是坏的。


:done
call "%~dp0port_lock.bat" release
echo.
echo 后端已退出。按任意键关闭窗口。
pause
