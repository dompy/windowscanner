@echo off
setlocal ENABLEDELAYEDEXPANSION

set "APP_DIR=%~dp0"
set "EXE=%APP_DIR%PraxisAssistantPsych.exe"

rem --- optional: Mark-of-the-Web entfernen (wenn aus ZIP heruntergeladen)
powershell -NoProfile -Command "Get-ChildItem '%APP_DIR%\PraxisAssistantPsych.exe','%APP_DIR%\start_PraxisAssistantPsych.bat' -ErrorAction SilentlyContinue | Unblock-File" >nul 2>&1

if not exist "%EXE%" (
  echo Fehler: EXE nicht gefunden unter:
  echo   "%EXE%"
  echo Bitte ZIP komplett entpacken und sicherstellen, dass EXE und BAT im selben Ordner liegen.
  pause
  exit /b 1
)

if "%OPENAI_API_KEY%"=="" (
  echo Kein OPENAI_API_KEY gefunden.
  set /p OPENAI_API_KEY=Bitte OpenAI API Key eingeben: 
  if "%OPENAI_API_KEY%"=="" (
    echo Kein Key eingegeben. Abbruch.
    pause
    exit /b 1
  )
  rem fuer zukuenftige Sitzungen persistent speichern:
  setx OPENAI_API_KEY "%OPENAI_API_KEY%" >nul
  echo Key gespeichert (Benutzer-Umgebungsvariable).
)

echo Starte Anwendung...
start "" "%EXE%"
exit /b 0
