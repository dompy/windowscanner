# Praxis-Assistent – Psychologie (Windows)

Ambulantes Tkinter-Tool zur strukturierten Dokumentation in der Psychologie:

* Ergänzende Anamnese (2–5 gezielte Fragen)
* Psychopathologischer Befund als Lückentext/Checkliste
* Einschätzung (Hypothesen, Schweregrad/Dringlichkeit)
* Prozedere (Interventionen, Sicherheit/Krisenplan, Verlauf)
* Red-Flags-Check (psychologische Regeln priorisiert)

---

## Voraussetzungen

* Python **3.11+**
* OpenAI API Key als Umgebungsvariable **`OPENAI_API_KEY`**

---

## Schnellstart (lokal)

```bash
python -m venv .venv
# macOS/Linux:
. .venv/bin/activate
# Windows (PowerShell):
# .venv\Scripts\Activate.ps1

pip install -r requirements_psychology.txt
python ui_assistant_stepflow.py
```

---

## Windows-Tester (ohne Python)

1. GitHub → **Actions** → letzter erfolgreicher Run → **Artifacts** → `PraxisAssistantPsych` (ZIP) herunterladen.
2. ZIP entpacken, dann:

   * **start\_PraxisAssistantPsych.bat** ausführen → fragt 1× nach **OPENAI\_API\_KEY** und setzt ihn als Benutzer-Variable.
   * **PraxisAssistantPsych.exe** starten.

> Hinweis: Ohne BAT kann der Key auch manuell gesetzt werden (Windows „Umgebungsvariablen für Ihr Konto“ → `OPENAI_API_KEY`).

---

## Build (GitHub Actions)

* Workflow: `.github/workflows/windows-build.yml`
* Trigger: Push auf **`psychology`** (oder manueller „Run workflow“)
* Output: OneFile-EXE als **Artifact** (optional zusätzlich als Release-Asset, falls ein Release erstellt wird)
* Eingepackte Ressourcen:

  * `psych_red_flags.json` (priorisiert)
  * `red_flags.json` (Fallback)

---

## Dateien (relevant)

* `ui_assistant_stepflow.py` – Tkinter-UI (ohne Word)
* `gpt_logic.py` – Psychologie-Prompts & Red-Flags-Resolver
* `red_flags_checker.py` – Matching-Logik
* `psych_red_flags.json` / `red_flags.json` – Regeln für Warnhinweise
* `requirements_psychology.txt` – minimale Abhängigkeiten (Windows-Build)
* `start_PraxisAssistantPsych.bat` – optionaler Windows-Launcher (API-Key-Abfrage)

---

## Datenschutz

Keine echten Patientendaten persistieren; Beispieltexte anonym halten. Red-Flags nur als Hinweisfeld – nicht automatisch in die Dokumentationsfelder einfügen.
