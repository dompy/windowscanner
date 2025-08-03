# word_reader.py

import os
import subprocess
from docx import Document

def get_active_word_path() -> str | None:
    """
    Gibt den Pfad des aktuell aktiven Word-Dokuments zurück.
    """
    try:
        script = '''
        tell application "Microsoft Word"
            if not (exists active document) then return ""
            set posixPath to POSIX path of (full name of active document)
            return posixPath
        end tell
        '''
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        path = result.stdout.strip()
        print("📤 AppleScript-Pfad-Rohwert:", result.stdout.strip())
        if path and os.path.exists(path):
            return path
        return None
    except Exception as e:
        print(f"❌ Fehler beim Abrufen des Word-Dateipfads: {e}")
        return None

def get_active_word_path_via_applescript() -> str | None:
    try:
        script = '''
        tell application "Microsoft Word"
            activate
            if not (exists active document) then
                return ""
            else
                return POSIX path of (get full name of active document)
            end if
        end tell
        '''
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        path = result.stdout.strip()
        return path if path else None
    except Exception as e:
        print(f"❌ Fehler beim AppleScript-Aufruf: {e}")
        return None

def get_word_text(path: str) -> str:
    """
    Extrahiert den reinen Text aus einem Word-Dokument.
    """
    try:
        doc = Document(path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as e:
        print(f"❌ Fehler beim Lesen der Word-Datei ({path}): {e}")
        return ""
