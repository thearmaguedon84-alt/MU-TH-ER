@echo off
REM Arrete toutes les instances de Jarvis, y compris les doublons.
REM Un second exemplaire garde les ports et repond a la place du bon :
REM c est la cause des 404 et des reponses qui semblent perimees.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*jarvis14.py*' } | ForEach-Object { Write-Host ('arret de ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }"
timeout /t 2 >nul
echo.
echo Jarvis est arrete.
pause
