@echo off
title Jarvis - Assistant Vocal
set PATH=C:\Users\thear\AppData\Local\Programs\Ollama;C:\Users\thear\AppData\Local\Programs\Python\Python313;%PATH%
cd /d C:\Users\thear\Documents\jarvis-assistant-vocal
echo Demarrage d'Ollama...
start /B "" "C:\Users\thear\AppData\Local\Programs\Ollama\ollama.exe" serve
timeout /t 2 /nobreak > nul
echo Lancement de Jarvis...
.venv\Scripts\python.exe -u jarvis14.py
pause
