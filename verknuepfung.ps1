# Legt im Startmenü die Verknüpfung "Speicheranalyse" an. Sie startet app.py ohne
# Konsolenfenster; die App öffnet den neuesten Bericht im Browser. Admin-Rechte wählt man
# dort pro Analyse (Windows fragt dann per UAC nach).
# Entfernen: "Speicheranalyse.lnk" im Startmenü-Ordner "Programme" löschen.
$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonw) {
    Write-Error 'pythonw.exe nicht gefunden. Ist Python installiert und im PATH?'
    exit 1
}
$ordner = [Environment]::GetFolderPath('Programs')
$shell = New-Object -ComObject WScript.Shell

$pfad = Join-Path $ordner 'Speicheranalyse.lnk'
$lnk = $shell.CreateShortcut($pfad)
$lnk.TargetPath = $pythonw
$lnk.Arguments = '-I "' + (Join-Path $PSScriptRoot 'app.py') + '"'
$lnk.WorkingDirectory = $PSScriptRoot
$lnk.IconLocation = "$env:SystemRoot\System32\shell32.dll,8"
$lnk.Description = 'Speicherbelegung von Laufwerken analysieren und als Bericht anzeigen'
$lnk.Save()
Write-Output "Angelegt: $pfad"

# Ältere Version dieses Skripts legte zusätzlich eine Admin-Verknüpfung an – die ist jetzt in der App
$alt = Join-Path $ordner 'Speicheranalyse (Admin).lnk'
if (Test-Path $alt) {
    Remove-Item $alt
    Write-Output "Entfernt: $alt"
}
