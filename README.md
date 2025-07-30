# 🪟🧠 WindowScanner

Ein lokal laufendes macOS-Tool zur Analyse geöffneter Fenster (PDFs, Word-Dokumente, Bildschirmbereiche via OCR) mit GPT-4.

## ✨ Features

- Erkennt geöffnete PDF- oder Word-Dateien auf deinem Mac
- Nutzt OCR (Tesseract), falls kein strukturierter Text vorliegt
- Extrahiert automatisch den Inhalt aus der Datei oder dem Fenster
- GPT-gestützte Analyse – du kannst beliebige Fragen stellen („Worum geht es in diesem PDF?“, „In welcher Sprache ist es geschrieben?“ etc.)
- Lokale Ausführung ohne Cloud-Speicherung

## 🚀 Quickstart

```bash
git clone https://github.com/dompy/windowscanner.git
cd windowscanner
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run_scan.py
```
🧑 Du wirst im Terminal gefragt, was du wissen möchtest (z. B. „Ist das PDF auf Englisch?“)

## 📦 Requirements
- macOS (Quartz-basiertes Window-Scanning)
- Python 3.11
- OpenAI API Key (als Umgebungsvariable OPENAI_API_KEY)

## 🔧 Verwendete Tools
- Quartz und AppKit – für Fensterzugriff auf macOS
- pytesseract – OCR-Texterkennung
- python-docx & PyMuPDF – Extraktion von Text aus Word und PDF
- OpenAI – GPT-4 Abfrage mit eigenem Prompt

## 🔒 Datenschutz
Alle Analysen laufen lokal. Es werden nur Texte extrahierter Fenster an OpenAI gesendet. Keine Inhalte werden dauerhaft gespeichert.

## 🛠️ To Do
- Unterstützung für mehrere offene PDFs gleichzeitig
- Optionale Filterung nach Fenstertitel
- Unterstützung für andere Dokumenttypen