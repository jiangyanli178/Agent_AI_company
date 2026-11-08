@echo off
chcp 936 >nul
title 停止全域策略研究所

echo ============================================
echo   正在停止：后端 8000 / 前端 5173
echo ============================================
echo.

call :killport 8000
call :killport 5173

echo.
echo 清理启动锁...
call "%~dp0port_lock.bat" release
echo   已释放

echo.
echo 清理 uvicorn 重载进程...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*uvicorn*app.main*' } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }" >nul 2>nul

echo 清理 vite 进程...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*node*vite*' } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }" >nul 2>nul

echo.
echo 完成。当前端口状态：
netstat -ano | findstr "LISTENING" | findstr ":8000 :5173"
echo （上面没有输出就说明两个端口都空出来了）

echo.
echo --------------------------------------------
echo 关于"窗口点了 X 关不掉"：
echo   cmd 窗口里只要用鼠标点过一下文本，就会进入"选择"模式，
echo   窗口标题会变成"选择 C:\Windows\system32\..."，进程被暂停。
echo   按一下 Esc（或点鼠标右键）退出选择模式，X 就正常了。
echo --------------------------------------------
echo.
pause
exit /b


:killport
set "PORT=%~1"
set "CNT=0"
for /f %%c in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING" ^| find /c /v ""') do set "CNT=%%c"
if "%CNT%"=="0" goto portfree
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    echo   端口 %PORT%：结束进程 %%a
    taskkill /F /T /PID %%a >nul 2>nul
)
goto :eof

:portfree
echo   端口 %PORT%：空闲
goto :eof
