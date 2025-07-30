# scanner.py
import os
import subprocess
from pathlib import Path
from typing import List
from PIL import Image
from docx import Document
import pytesseract
from PyPDF2 import PdfReader
from openai import OpenAI
from Quartz import CGWindowListCopyWindowInfo, kCGWindowListOptionOnScreenOnly, kCGNullWindowID

# OpenAI-Client initialisieren
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise EnvironmentError("❌ Umgebungsvariable OPENAI_API_KEY ist nicht gesetzt!")
client = OpenAI(api_key=api_key)

def get_visible_window_titles() -> List[str]:
    options = kCGWindowListOptionOnScreenOnly
    window_list = CGWindowListCopyWindowInfo(options, kCGNullWindowID)
    return [win.get('kCGWindowName', '') for win in window_list if win.get('kCGWindowName')]

def get_word_active_document_path() -> str:
    try:
        script = '''
        tell application "Microsoft Word"
            if not (exists active document) then return ""
            set posixPath to POSIX path of (full name of active document)
            return posixPath
        end tell
        '''
        output = subprocess.check_output(["osascript", "-e", script])
        return output.decode().strip()
    except Exception as e:
        return f"❌ Fehler bei Word-Dateipfad: {e}"

def extract_text_from_docx(path: str) -> str:
    try:
        doc = Document(path)
        return "\n".join(p.text for p in doc.paragraphs)
    except Exception as e:
        return f"❌ Fehler beim Lesen der Word-Datei: {e}"

def get_visible_window_text_ocr(temp_path: Path) -> str:
    screenshot_file = temp_path / "screenshot.png"
    os.makedirs(temp_path, exist_ok=True)
    subprocess.run(["screencapture", "-x", str(screenshot_file)])
    image = Image.open(screenshot_file)
    return pytesseract.image_to_string(image, lang='deu+eng').strip()

def get_open_pdfs_from_preview() -> list[str]:
    """Liefert alle PDF-Dateien, die Preview aktuell geöffnet hat (via lsof)."""
    try:
        output = subprocess.check_output(["lsof", "-c", "Preview", "-Fn"])
        lines = output.decode().splitlines()
        pdfs = [line[1:] for line in lines if line.startswith('n') and line.lower().endswith('.pdf')]
        return list(set(pdfs))  # doppelte entfernen
    except Exception as e:
        return [f"❌ Fehler bei lsof: {e}"]

def extract_text_from_pdf(path: str) -> str:
    """Liest Text aus einem PDF-Dokument."""
    try:
        reader = PdfReader(path)
        return "\n".join([page.extract_text() or "" for page in reader.pages])
    except Exception as e:
        return f"❌ Fehler beim Lesen der PDF-Datei: {e}"

def split_pdf_into_chunks(path: str, max_chars=1000) -> list[str]:
    reader = PdfReader(path)
    text = "\n".join([p.extract_text() or "" for p in reader.pages])
    paragraphs = text.split("\n\n")
    chunks = []
    current = ""
    for p in paragraphs:
        if len(current) + len(p) < max_chars:
            current += "\n\n" + p
        else:
            chunks.append(current.strip())
            current = p
    if current:
        chunks.append(current.strip())
    return chunks

# Neue Funktion: relevante Chunks nach Frage filtern

def select_relevant_chunks(chunks: list[str], question: str, top_n=3, max_total_chars=4000) -> str:
    stopwords = {"was", "wie", "ist", "sind", "ein", "eine", "der", "die", "das", "für", "mit"}
    keywords = [w for w in question.lower().split() if w not in stopwords]

    scored = []
    for chunk in chunks:
        score = sum(chunk.lower().count(k) for k in keywords)
        scored.append((score, chunk))

    scored.sort(reverse=True)
    selected = [chunk for score, chunk in scored if score > 0][:top_n]
    if not selected:
        selected = [chunk for _, chunk in scored[:top_n]]

    # Prompt-Länge begrenzen
    result = []
    total_chars = 0
    for chunk in selected:
        if total_chars + len(chunk) > max_total_chars:
            break
        result.append(chunk)
        total_chars += len(chunk)

    return "\n\n".join(result)

def build_prompt(extracted_text: str, question: str) -> str:
    return f"""Der folgende Text wurde aus einem geöffneten Fenster extrahiert:

<<<
{extracted_text}
>>>

Bitte beantworte folgende Frage:
{question}
"""

def ask_openai(prompt: str) -> str:
    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Du bist ein medizinischer Assistent. Du antwortest konzise und versuchst stets, die wichtigsten Informationen zu liefern."},
                {"role": "user", "content": prompt}
            ]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"❌ Fehler bei OpenAI-Anfrage: {e}"
