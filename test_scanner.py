# test_scanner.py
from scanner import extract_text_from_docx, extract_text_from_pdf, build_prompt
from pathlib import Path

def test_extract_text_from_docx():
    path = Path("Projektplan_KI_Praxis_Tool_aktualisiert.docx")
    assert path.exists()
    text = extract_text_from_docx(str(path))
    assert isinstance(text, str)
    assert len(text) > 10

def test_build_prompt():
    ctx = "Testtext aus Dokument"
    q = "Was steht hier?"
    result = build_prompt(ctx, q)
    assert ctx in result
    assert q in result

def test_extract_text_from_pdf():
    path = "test_beispiel.pdf"
    assert os.path.exists(path)
    text = extract_text_from_pdf(path)
    assert isinstance(text, str)
    assert len(text) > 10