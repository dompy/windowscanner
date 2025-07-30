# run_scan.py
from pathlib import Path
import os
import zipfile
import subprocess
import traceback
from scanner import (
    get_visible_window_titles,
    get_open_pdfs_from_preview,
    extract_text_from_docx,
    extract_text_from_pdf,
    split_pdf_into_chunks,
    select_relevant_chunks,
    get_visible_window_text_ocr,
    build_prompt,
    ask_openai,
)

def find_all_docx_paths_in_titles(titles):
    docx_paths = []
    for title in titles:
        if title.endswith(".docx"):
            possible_path = Path.home() / "Documents" / title
            if possible_path.exists() and zipfile.is_zipfile(possible_path):
                docx_paths.append(str(possible_path))
    return docx_paths

def get_word_active_document_path_via_applescript() -> str | None:
    try:
        script = '''
        tell application "Microsoft Word"
            if not (exists active document) then
                return ""
            end if
            return (get full name of active document) as string
        end tell
        '''
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if result.stderr:
            print(f"⚠️ AppleScript-Fehler: {result.stderr.strip()}")
        print(f"📤 AppleScript-Ausgabe: '{result.stdout.strip()}'")
        path = result.stdout.strip()
        # AppleScript gibt Pfade mit Doppelpunkten zurück (Classic Mac Style)
        if path:
            components = path.split(":")
            if components[0] == "Macintosh HD":
                unix_path = "/" + "/".join(components[1:])
                if os.path.exists(unix_path):
                    return unix_path
                else:
                    print("⚠️ Der konvertierte Pfad existiert nicht:", unix_path)
    except Exception as e:
        print(f"⚠️ Ausnahme beim Zugriff auf Word-Dateipfad: {e}")
    return None

def load_all_window_texts(temp_dir: Path) -> str:
    titles = get_visible_window_titles()
    print(f"📋 Fenster erkannt: {titles}")

    all_texts = []

    # AppleScript: aktives Word-Dokument
    word_path = get_word_active_document_path_via_applescript()
    if word_path:
        print(f"📄 Word erkannt (AppleScript): {word_path}")
        try:
            text = extract_text_from_docx(word_path).strip()
            if text:
                all_texts.append(f"--- WORD (aktiv): {os.path.basename(word_path)} ---\n{text}")
        except Exception as e:
            print(f"⚠️ Fehler beim Lesen von {word_path}: {e}")

    # Zusätzlich: potenzielle .docx-Titel aus Finder
    docx_paths = find_all_docx_paths_in_titles(titles)
    for path in docx_paths:
        print(f"📄 Word erkannt: {path}")
        try:
            text = extract_text_from_docx(path).strip()
            if text:
                all_texts.append(f"--- WORD (aus Finder): {os.path.basename(path)} ---\n{text}")
        except Exception as e:
            print(f"⚠️ Fehler beim Lesen von {path}: {e}")

    # PDFs
    pdf_paths = get_open_pdfs_from_preview()
    if pdf_paths:
        for pdf_path in pdf_paths:
            if os.path.exists(pdf_path):
                print(f"📄 PDF erkannt: {pdf_path}")
                raw_text = extract_text_from_pdf(pdf_path)
                if raw_text.strip():
                    print(f"🗘️ PDF-Textlänge: {len(raw_text)} Zeichen")
                    chunks = [raw_text[i:i+1000] for i in range(0, len(raw_text), 1000)]
                    all_texts.append(f"--- PDF: {os.path.basename(pdf_path)} ---\n" + "\n\n".join(chunks[:1]).strip())
                else:
                    print("⚠️ Extrahierter PDF-Text war leer – OCR-Fallback.")
                    all_texts.append(get_visible_window_text_ocr(temp_dir).strip())

    if not all_texts:
        print("🔍 Kein Word/PDF – OCR-Fallback.")
        all_texts.append(get_visible_window_text_ocr(temp_dir).strip())

    return "\n\n---\n\n".join(filter(None, all_texts))

def main():
    temp_dir = Path("/tmp/window_scanner")
    print("\n🧪 Starte run_scan.py")
    print("📸 Fenster werden beobachtet. Du kannst Fragen zu geöffneten Dokumenten stellen.")

    while True:
        user_prompt = input("\n❓ Deine Frage (oder 'wechsel', 'exit'): ").strip()

        if user_prompt.lower() in {"exit", "quit", "q"}:
            print("👋 Beende den WindowScanner.")
            break

        elif user_prompt.lower() in {"wechsel", "wechseln", "refresh", "reload"}:
            print("🔄 Manuelles Nachladen...")
            combined_text = load_all_window_texts(temp_dir)
            if combined_text:
                print("📄 Verwendeter Text (gekürzt):")
                print(combined_text[:500] + "...\n")
            else:
                print("⚠️ Es konnte kein Text extrahiert werden.")
            continue

        # Erst jetzt Texte aus Fenstern laden
        print("📅 Lade Text aus geöffneten Fenstern...")
        combined_text = load_all_window_texts(temp_dir)

        if not combined_text.strip():
            print("⚠️ Kein Text extrahiert. Bitte stelle sicher, dass relevante Dateien geöffnet sind.")
            continue

        final_prompt = build_prompt(combined_text, user_prompt)
        if len(final_prompt) > 12000:
            print("⚠️ Der Prompt ist zu lang und wird nicht gesendet.")
            continue

        print("📨 Anfrage an OpenAI wird gesendet...")
        antwort = ask_openai(final_prompt)

        print("\n💡 Antwort von GPT-4:")
        print(antwort)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
