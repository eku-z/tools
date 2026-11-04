@echo off
rem Run every Tools test (offscreen, scratch data folder, stand-in Resolve).
cd /d "%~dp0"
py -3.12 tests\run_tests.py %*
pause
