@echo off
rem Start Tools with the Python 3.12 that has PySide6, NumPy and PyAV, without a
rem console window (pyw). This window closes at once. To see errors, run:
rem py -3.12 -m tools
cd /d "%~dp0"
start "" pyw -3.12 -m tools %*
