@echo off
setlocal enableextensions enabledelayedexpansion

set "APP_DIR=%~dp0"
set "EXE=%APP_DIR%PraxisAssistantPsych.exe"
if not exist "%EXE%" if exist "%APP_DIR%dist\PraxisAssistantPsych.exe" set "EXE=%APP_DIR%dist\PraxisAssistantPsych.exe"

rem Optional: Mark-of-the-Web entfernen
powershell -NoProfile -Command "Get-ChildItem '%EXE%','%~f0' -ErrorAction SilentlyContinue | Unblock-File" >nul 2>&1

rem ---- Testmodus (für CI / Vorabcheck) ----
if /I "%~1"=="test" goto :SMOKE

rem ---- Normalstart ----
if not exist "%EXE%" goto :NOEXE

if "%OPENAI_API_KEY%"=="" (
  echo Kein OPENAI_API_KEY gefunden.
  set /p OPENAI_API_KEY=Bitte OpenAI API Key eingeben:
  if "%OPENAI_API_KEY%"=="" goto :NOKEY
  setx OPENAI_API_KEY "%OPENAI_API_KEY%" >nul
  echo Key gespeichert (Benutzer-Umgebungsvariable).
)

echo Starte Anwendung...
"%EXE%"
set "RC=%ERRORLEVEL%"
echo Rueckgabecode: %RC%
if not "%RC%"=="0" echo Hinweis: ggf. SmartScreen/Unblock und OPENAI_API_KEY pruefen.& pause
exit /b %RC%

:SMOKE
if "%OPENAI_API_KEY%"=="" set "OPENAI_API_KEY=dummy"
"%EXE%" --smoke-test
echo SMOKE_EXIT=%ERRORLEVEL%
exit /b %ERRORLEVEL%

:NOEXE
echo Fehler: EXE nicht gefunden:
echo   %EXE%
echo Bitte ZIP komplett entpacken; EXE und BAT in denselben Ordner legen.
pause
exit /b 1

:NOKEY
echo Kein Key eingegeben. Abbruch.
pause
exit /b 1
