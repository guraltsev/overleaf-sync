@echo off
setlocal

rem Run the checkout directly, without installing the overleaf-sync package.
set "PYTHONPATH=%~dp0olsync;%PYTHONPATH%"
python -m olsync.olsync %*
exit /b %ERRORLEVEL%
