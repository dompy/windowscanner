# Praxis-Assistent – Psychologie (Windows)

Ambulantes Tkinter-Tool zur strukturierten Dokumentation (Anamnese-Zusatzfragen, psychopathologischer Befund als Lückentext, Einschätzung, Prozedere) mit Red-Flags-Check (Psychologie bevorzugt).

## Voraussetzungen
- Python 3.11+
- OpenAI API Key als Umgebungsvariable `OPENAI_API_KEY`

## Schnellstart (lokal)
```bash
python -m venv .venv
. .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements_psychology.txt
python ui_assistant_stepflow.py