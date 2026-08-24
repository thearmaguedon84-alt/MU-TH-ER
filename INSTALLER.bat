@echo off
chcp 65001 >nul
title Installation de Jarvis
cd /d "%~dp0"

REM Double-cliquer un fichier .py ouvre souvent l editeur plutot que de le
REM lancer : ce raccourci garantit que l assistant demarre vraiment, et que
REM la fenetre reste ouverte si quelque chose se passe mal.

where python >nul 2>&1
if errorlevel 1 (
  echo.
  echo   Python n est pas installe, ou n est pas dans le PATH.
  echo.
  echo   Telecharge-le sur python.org et coche bien
  echo   "Add Python to PATH" pendant l installation.
  echo.
  pause
  exit /b 1
)

python installer.py
if errorlevel 1 (
  echo.
  echo   L installation s est interrompue. Le message ci-dessus dit pourquoi.
  pause
)
