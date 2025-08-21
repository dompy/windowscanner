@echo off
setlocal
if "%OPENAI_API_KEY%"=="" (
  echo Bitte OpenAI API Key eingeben (wird lokal gespeichert):
  set /p OPENAI_API_KEY=API Key:
  setx OPENAI_API_KEY "%OPENAI_API_KEY%"
  echo OK. Bitte App neu starten, damit der Key verfuegbar ist.
  pause
  exit /b
)
start "" "%~dp0PraxisAssistantPsych.exe"

