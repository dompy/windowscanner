@echo off
setlocal ENABLEDELAYEDEXPANSION

set "APP_DIR=%~dp0"
set "EXE=%APP_DIR%PraxisAssistantPsych.exe"
if not exist "%EXE%" if exist "%APP_DIR%dist\PraxisAssistantPsych.exe" set "EXE=%APP_DIR%dist\PraxisAssistantPsych.exe"

rem Mark-of-the-Web entfernen (optional, falls aus ZIP)
powershell -NoProfile -Command "Get-ChildItem '%EXE%','%~f0' -ErrorAction SilentlyContinue | Unblock-File" >nul 2>&1

if not exist "%EXE%" (
  echo Fehler: EXE nicht gefunden unter:
  echo   "%EXE%"
  echo Bitte ZIP komplett entpacken und EXE/BAT im selben Ordner ablegen.
  pause
  exit /b 1
)

rem --- OPENAI_API_KEY abfragen, falls nicht vorhanden ---
if "%OPENAI_API_KEY%"=="" (
  echo Kein OPENAI_API_KEY gefunden.
  set /p OPENAI_API_KEY=Bitte OpenAI API Key eingeben: 
  if "%OPENAI_API_KEY%"=="" (
    echo Kein Key eingegeben. Abbruch.
    pause
    exit /b 1
  )
  rem Sofort im aktuellen Prozess verfügbar + fuer Zukunft speichern
  setx OPENAI_API_KEY "%OPENAI_API_KEY%" >nul
  echo Key gespeichert (Benutzer-Umgebungsvariable).
)

echo Starte Anwendung...
"%EXE%"
set "RC=%ERRORLEVEL%"
echo Rueckgabecode: %RC%
if not "%RC%"=="0" (
  echo Hinweis: Falls die App sofort schliesst, pruefe SmartScreen/Unblock und den OPENAI_API_KEY.
  pause
)
exit /b %RC%
