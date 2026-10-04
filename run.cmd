@echo off
rem 开发模式直接启动（不用打包）
setlocal
set "PROJ=%~dp0"
set "PY=%USERPROFILE%\.conda\envs\imtag\pythonw.exe"
if not exist "%PY%" set "PY=%USERPROFILE%\.conda\envs\imtag\python.exe"
set "IMGTAG_HOME=%PROJ%"
set "PYTHONUTF8=1"
start "" "%PY%" -m app.main
