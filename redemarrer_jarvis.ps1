# Relance Jarvis : arrete toutes les instances puis relance lancer_jarvis.bat.
# Appele par le bouton "Redemarrer l IA" du telephone, ou a la main.
$ici = Split-Path -Parent $MyInvocation.MyCommand.Path
$trace = Join-Path $ici "logs\redemarrage.log"
function Note($m) { Add-Content -Path $trace -Value ((Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "  " + $m) }

# Laisse a la page le temps de recevoir sa reponse avant de couper le serveur.
Start-Sleep -Seconds 2
Note "redemarrage demande"
# Seulement les lignes de commande qui FINISSENT par le nom : un outil qui
# se contente de citer jarvis14.py ne doit pas etre tue.
Get-CimInstance Win32_Process | Where-Object {
    ($_.CommandLine -match 'jarvis14\.py\s*$' -or $_.CommandLine -match 'lancer_jarvis\.bat"?\s*$') -and $_.ProcessId -ne $PID
} | ForEach-Object {
    Note ("arret de " + $_.ProcessId + " (" + $_.Name + ")")
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 3
Start-Process -FilePath "cmd.exe" -ArgumentList '/c', ('"' + (Join-Path $ici "lancer_jarvis.bat") + '"') -WorkingDirectory $ici
Note "lancer_jarvis.bat relance"
