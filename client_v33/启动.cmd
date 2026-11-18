@echo off
rem 抢单助手 v3.3 无控制台启动器
rem 用 pythonw.exe 启动，不弹命令行窗口；出错信息落到 logs\stderr.log
setlocal
cd /d "%~dp0"

set "PYW=D:\python115\pythonw.exe"
if not exist "%PYW%" (
    for %%P in (pythonw.exe) do set "PYW=%%~$PATH:P"
)
if not exist "%PYW%" (
    set "PYW=pythonw.exe"
)

if not exist "logs" mkdir "logs"

start "" "%PYW%" -X utf8 "main.py" 2>"logs\stderr.log"
exit /b 0
