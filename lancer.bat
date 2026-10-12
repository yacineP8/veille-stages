@echo off
rem Collecte les offres puis ouvre la page de suivi.
chcp 65001 >nul
cd /d "%~dp0"
set "PY=python"
for /d %%D in ("%LOCALAPPDATA%\Python\pythoncore-*") do if exist "%%D\python.exe" set "PY=%%D\python.exe"
echo Collecte des offres en cours (1 a 2 minutes)...
"%PY%" collecte.py
if errorlevel 1 (
  echo.
  echo La collecte a echoue. Verifiez la connexion internet et que Python est installe.
  pause
  exit /b 1
)
start "" "%~dp0index.html"
