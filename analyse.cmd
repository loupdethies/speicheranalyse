@echo off
rem Speicheranalyse: scannen, klassifizieren, abgleichen, Bericht öffnen.
rem Optional anderes Laufwerk: analyse.cmd E:\
cd /d "%~dp0"
set ROOT=%~1
if "%ROOT%"=="" set ROOT=C:
rem abschließenden Backslash entfernen, sonst maskiert er das Anführungszeichen
if "%ROOT:~-1%"=="\" set ROOT=%ROOT:~0,-1%
python -I scanner.py --root "%ROOT%" || goto :err
python -I classify.py || goto :err
python -I abgleich.py || goto :err
python -I report.py --open || goto :err
exit /b 0
:err
echo Fehler - siehe Ausgabe oben.
pause
exit /b 1
