@echo off
setlocal ENABLEDELAYEDEXPANSION

set "APP_DIR=%~dp0"
set "EXE=%APP_DIR%PraxisAssistantPsych.exe"
if not exist "%EXE%" if exist "%APP_DIR%dist\PraxisAssistantPsych.exe" set "EXE=%APP_DIR%dist\PraxisAssistantPsych.exe"

rem Mark-of-the-Web entfernen (optional)
powershell -NoProfile -Command "Get-ChildItem '%EXE%','%~f0' -ErrorAction SilentlyContinue | Unblock-File" >nul 2>&1

rem ---- Testmodus: nur Voraussetzungen pruefen, keine GUI ----
if /I "%1"=="test" (
  if "%OPENAI_API_KEY%"=="" set "OPENAI_API_KEY=dummy"
  "%EXE%" --smoke-test
  echo SMOKE_EXIT=%ERRORLEVEL%
  exit /b %ERRORLEVEL%
)

if not exist "%EXE%" (
  echo Fehler: EXE nicht gefunden unter:
  echo   "%EXE%"
  echo Bitte ZIP komplett entpacken; EXE und BAT im selben Ordner.
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
  setx OPENAI_API_KEY "%OPENAI_API_KEY%" >nul
  echo Key gespeichert (Benutzer-Umgebungsvariable).
)

echo Starte Anwendung...
start "" "%EXE%"
exit /b 0
