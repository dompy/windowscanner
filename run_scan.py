# run_scan.py
from pathlib import Path
import os
import zipfile
import traceback
from scanner import (
    get_visible_window_titles,
    get_word_active_document_path,
    get_open_pdfs_from_preview,
    extract_text_from_docx,
    extract_text_from_pdf,
    split_pdf_into_chunks,
    select_relevant_chunks,
    get_visible_window_text_ocr,
    build_prompt,
    ask_openai,
)

def main():
    temp_dir = Path("/tmp/window_scanner")
    print("\n🧪 Starte run_scan.py")
    print("📸 Scanning visible windows and extracting text...")

    titles = get_visible_window_titles()
    print(f"📋 Fenster erkannt: {titles}")

    user_prompt = input("\n🧑 Was möchtest du basierend auf den geöffneten Fenstern wissen? → ")

    combined_text = ""
    word_path = get_word_active_document_path()
    pdf_paths = get_open_pdfs_from_preview()

    if word_path and word_path.endswith(".docx") and os.path.exists(word_path) and zipfile.is_zipfile(word_path):
        print(f"📄 Word erkannt: {word_path}")
        combined_text = extract_text_from_docx(word_path)
    elif pdf_paths:
        for pdf_path in pdf_paths:
            if os.path.exists(pdf_path):
                print(f"📄 PDF erkannt: {pdf_path}")
                raw_text = extract_text_from_pdf(pdf_path)
                chunks = [raw_text[i:i+1000] for i in range(0, len(raw_text), 1000)]
                print(f"📦 Anzahl Chunks: {len(chunks)}")
                combined_text = "\n\n".join(chunks[:1])  # Für Sprachbestimmung reicht oft 1 Chunk
                break
        else:
            print("⚠️ Kein gültiges PDF – OCR-Fallback.")
            combined_text = get_visible_window_text_ocr(temp_dir)
    else:
        print("🔍 Kein Word/PDF – OCR-Fallback.")
        combined_text = get_visible_window_text_ocr(temp_dir)

    print("📄 Verwendeter Text (gekürzt):")
    print(combined_text[:500] + "...\n")

    final_prompt = build_prompt(combined_text, user_prompt)

    if len(final_prompt) > 12000:
        print("⚠️ Der Prompt ist zu lang und wird nicht an GPT-4 gesendet.")
        return

    print("\n🧠 Finaler Prompt an GPT-4:")
    print(final_prompt[:500] + "...\n")

    print("\n📨 Anfrage an OpenAI wird gesendet...")
    antwort = ask_openai(final_prompt)

    print("\n💡 Antwort von GPT-4:")
    print(antwort)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
