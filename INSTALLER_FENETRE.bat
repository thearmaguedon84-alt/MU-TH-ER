@echo off
chcp 65001 >nul
title Installation de Jarvis
cd /d "%~dp0"

REM Version en fenetre. La version console reste disponible : INSTALLER.bat

where python >nul 2>&1
if errorlevel 1 (
  echo.
  echo   Python n est pas installe, ou n est pas dans le PATH.
  echo   Telecharge-le sur python.org en cochant "Add Python to PATH".
  echo.
  pause
  exit /b 1
)

start "" pythonw installer_fenetre.py
