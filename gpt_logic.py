# gpt_logic.py  — CLEAN & SWISS-STYLE
 
# gestern abend schüttelfrost, dann gliederschmerzen, fieber 39°, verwirrt gewesen, auf dafalgan fieber regredient.
# seit 3 tagen husten mit gelb-grünem auswurf, atemnot beim treppensteigen, letzte nacht leicht fieber, kein brustschmerz.
# seit heute morgen starke schmerzen im rechten unterbauch, übelkeit, kein erbrechen, kein durchfall, kein fieber gemessen.
# vor einer woche umgeknickt, seitdem schwellung und schmerz am rechten sprunggelenk, belastung kaum möglich, keine offene wunde.
# seit 2 wochen müde, blass, appetitlos, in letzter zeit häufig schwindel beim aufstehen, keine magen-darm-beschwerden.
# seit gestern juckender ausschlag an beiden armen, nach gartenarbeit aufgetreten, keine atemnot, kein fieber.

import os
import json
import tkinter as tk
from tkinter import scrolledtext, messagebox
from typing import Any, Dict, List, Optional, Tuple
from openai import OpenAI
from red_flags_checker import check_red_flags, load_red_flags

# ------------------ Config & Client ------------------

MODEL_DEFAULT = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise EnvironmentError("❌ Umgebungsvariable OPENAI_API_KEY ist nicht gesetzt!")

client = OpenAI(api_key=api_key)

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
RED_FLAGS_PATH = os.path.join(THIS_DIR, "red_flags.json")

# Globaler Stil (kann in einzelnen Funktionen ergänzt werden)
PROMPT_PREFIX = (
    "Beziehe dich auf anerkannte medizinische Guidelines (z. B. smarter medicine, SSGIM, EBM, Hausarztmedizin Schweiz). "
    "Antworte bevorzugt stichpunktartig (ausser explizit Sätze gefordert), kurz, präzise, praxisnah (Schweiz). "
    "Schweizer Orthografie (ss statt ß)."
)


# ------------------ Low-level Helpers ------------------

def ask_openai(prompt: str) -> str:
    """Einfacher Wrapper (Deutsch erzwungen, kurz & präzise)."""
    resp = client.chat.completions.create(
        model=MODEL_DEFAULT,
        messages=[
            {"role": "system", "content": "Antworte ausschließlich auf Deutsch. Knapp, präzise, praxisnah."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    return resp.choices[0].message.content.strip()


def _ask_openai_json(
    messages: List[Dict[str, str]],
    model: str = MODEL_DEFAULT,
    temperature: float = 0.2
) -> Dict[str, Any]:
    """Antwort als JSON-Objekt erzwingen (mit Fallback)."""
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or "{}"
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {"raw_text": content}


def _swiss_style_note(humanize: bool = True) -> str:
    base = (
        "Schweizer Orthografie (ss statt ß). "
        "Natürlich klingend wie hausärztliche KG-Einträge, kurz/telegraphisch; "
    )
    if humanize:
        base += "gelegentlich minimale Tippfehler/Verkürzungen einbauen; "
    base += "keine Floskeln, keine Romane."
    return base


# ------------------ 4 Felder – fix & fertig ------------------

def _format_full_entries_block(payload: Dict[str, Any]) -> str:
    """Kopierfertiger Block mit allen vier Feldern (Red Flags separat im UI)."""
    parts = []
    parts.append("Anamnese:")
    parts.append((payload.get("anamnese_text") or "keine Angaben").strip())
    parts.append("")
    parts.append("Befunde:")
    parts.append((payload.get("befunde_text") or "keine Angaben").strip())
    parts.append("")
    parts.append("Beurteilung:")
    parts.append((payload.get("beurteilung_text") or "keine Angaben").strip())
    parts.append("")
    parts.append("Prozedere:")
    parts.append((payload.get("prozedere_text") or "keine Angaben").strip())
    return "\n".join(parts).strip()


def generate_full_entries_german(
    user_input: str,
    context: Optional[Dict[str, Any]] = None
) -> Tuple[Dict[str, str], str]:
    """
    Baut vier dokumentationsfertige Felder (Deutsch):
      - anamnese_text, befunde_text, beurteilung_text, prozedere_text
    Gibt zusätzlich red_flags im Payload zurück (für ein getrenntes Warnfeld im UI).
    """
    context = context or {}

    # Red Flags lokal prüfen (separat im UI anzeigen)
    try:
        red_flags_data = load_red_flags(RED_FLAGS_PATH)
        rf_hits = check_red_flags(user_input, red_flags_data, return_keywords=True) or []
        red_flags_list = [f"{kw} – {msg}" for (kw, msg) in rf_hits]
    except Exception:
        red_flags_list = []

    # System-Prompt (kein f-string, damit wir sicher vor Backslash-Problemen sind)
    sys_msg = (
        "Du bist ein erfahrener Hausarzt in einer Schweizer Hausarztpraxis.\n"
        "Ziel: Erzeuge vier dokumentationsfertige Felder (Deutsch), direkt kopierbar.\n"
        "WICHTIG:\n"
        "- Nichts erfinden. Wo Angaben fehlen: \"keine Angaben\", \"nicht erhoben\" oder \"noch ausstehend\".\n"
        "- Stil:\n"
        "  • Anamnese: kurz/telegraphisch; Dauer, Lokalisation/Qualität, relevante zu erfragende Begleitsymptome auflisten, relevante zu erfragende Vorerkrankungen/Medikation auflisten, Kontext.\n"
        "  • Befunde: objektiv; Kurzstatus (AZ).\n"
        "  • Beurteilung: Verdachtsdiagnose + 2–4 DD (kurz, plausibel).\n"
        "  • Prozedere: kurze, klare Bulletpoints; nächste Schritte, Verlauf/Kontrolle, Vorzeitige Wiedervorstellung; Medikation nur allgemein, keine erfundenen Dosierungen.\n"
        "- Schweizer Orthografie (ss statt ß), kein z.B., Natürlich/knapp.\n"
        "- Antworte ausschließlich als JSON:\n\n"
        "{\n"
        "  \"anamnese_text\": \"string\",\n"
        "  \"befunde_text\": \"string\",\n"
        "  \"beurteilung_text\": \"string\",\n"
        "  \"prozedere_text\": \"string\"\n"
        "}\n"
    ).strip()

    usr_payload = {"eingabetext": user_input, "kontext": context}

    result = _ask_openai_json(
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": json.dumps(usr_payload, ensure_ascii=False)},
        ]
    )

    # Red Flags nur anhängen (UI zeigt separat)
    if red_flags_list:
        result["red_flags"] = red_flags_list

    full_block = _format_full_entries_block(result)
    return result, full_block


# ------------------ Schritt 1: Anamnese → Lückentext ------------------

def generate_anamnese_gaptext_german(
    anamnese_raw: str,
    answered_context: Optional[str] = "",
    humanize: bool = True
) -> Tuple[Dict[str, Any], str]:
    """
    Erzeugt 2–3 gezielte Zusatzfragen basierend auf dem Patiententext.
    Return: (payload, fragen_text)
    payload: { "zusatzfragen": [..] }
    """

    def _sys_msg_base(note: str) -> str:
        return (
            "Du bist ein erfahrener Hausarzt in der Schweiz.\n"
            + note + "\n"
            "Aufgabe: Analysiere den Freitext des Patienten und formuliere **2–5 gezielte, medizinisch relevante Zusatzfragen**, "
            "um die wahrscheinlichste Diagnose schnell einzugrenzen.\n"
            "Keine Untersuchungen nennen – nur Fragen.\n"
            "Fragen müssen kurz, klar und patientenverständlich formuliert sein.\n"
            "Antwort ausschließlich als JSON im Format:\n"
            "{\n"
            "  \"zusatzfragen\": [\"Frage 1\", \"Frage 2\", \"Frage 3\", \"Frage 4\", \"Frage 5\"]\n"
            "}\n"
        ).strip()

    note = _swiss_style_note(humanize)
    sys_msg = _sys_msg_base(note)

    usr = {
        "eingabe_freitext": anamnese_raw,
        "bereits_beantwortet": answered_context or "",
        "hinweise": "Keine Lückentexte, keine Listen mit Untersuchungen. Fokus nur auf Zusatzfragen."
    }

    result = _ask_openai_json(
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": json.dumps(usr, ensure_ascii=False)},
        ]
    )

    fragen_text = ""
    if isinstance(result, dict):
        fragen_liste = result.get("zusatzfragen", [])
        if fragen_liste:
            fragen_text = "\n".join([f"- {f}" for f in fragen_liste])

    return result, fragen_text or anamnese_raw


def generate_befunde_gaptext_german(
    anamnese_filled: str,
    humanize: bool = True,
    phase: str = "initial"  # "initial" oder "persistent"
) -> Tuple[Dict[str, Any], str]:
    """
    Liefert praxisnahe Befunde als Lückentext/Checkliste zum direkten Ausfüllen
    (kein fertiger Status-Fliesstext). Return: (payload, befunde_lueckentext).
    payload: {"befunde_lueckentext": str, "befunde_checkliste": [..]}
    """
    note = _swiss_style_note(humanize)

    sys_msg = (
        "Du bist ein erfahrener Hausarzt in einer Schweizer Hausarztpraxis.\n"
        + note + "\n"
        "Aufgabe: Erzeuge eine Liste praxisrelevanter körperlicher Untersuchungen, die in der Hausarztpraxis zu erheben sind und "
        "zur Anamnese passen. Keine Vitalparameter!\n"
        "WICHTIG:\n"
        "- KEIN fertiger Statusbericht / kein Fliesstext.\n"
        "- Nur ausfüllbare Punkte mit Platzhaltern/Optionen, z. B.:\n"
        "- Keine konkreten Messwerte eintragen, nur Struktur zum Ausfüllen.\n"
        "- Nichts doppeln, was in der Anamnese bereits beantwortet ist.\n"
        "- phase=\"initial\": nur Basics; phase=\"persistent\": am Ende eine Zusatzzeile mit 2–3 sinnvollen Erweiterungen.\n"
        "Antwort ausschließlich als JSON:\n"
        "{\n"
        "  \"befunde_lueckentext\": \"string\",\n"
        "  \"befunde_checkliste\": [\"string\", \"...\"]\n"
        "}\n"
    ).strip()

    usr = {
        "anamnese_abgeschlossen": anamnese_filled,
        "phase": phase
    }

    result = _ask_openai_json(
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": json.dumps(usr, ensure_ascii=False)},
        ]
    )

    bef_text = ""
    if isinstance(result, dict):
        bef_text = (result.get("befunde_lueckentext") or "").strip()

    # Fallback, falls das Modell nichts liefert: generische, ausfüllbare Skeleton-Liste
    if not bef_text:
        lines = [
            "- AZ: gut mittel reduziert",
            "- keine weiteren Befunde",
        ]
        if phase == "persistent":
            lines.append('Bei Persistenz/Progredienz: (Röntgen/US/erweitertes Labor) __')
        bef_text = "\n".join(lines)

    return result, bef_text


def on_gaptext(self):
    raw = self.fields.get("Anamnese").get("1.0", tk.END).strip() if "Anamnese" in self.fields else ""
    if not raw:
        from tkinter import messagebox
        messagebox.showwarning("Hinweis", "Bitte zuerst Anamnese (frei) eingeben.")
        return
    try:
        payload, gap = generate_anamnese_gaptext_german(raw)
    except Exception as e:
        from tkinter import messagebox
        messagebox.showerror("Fehler", f"Lückentext fehlgeschlagen:\n{e}")
        return
    self.txt_gap.delete("1.0", tk.END)
    self.txt_gap.insert(tk.END, gap)  # <— integrierter Lückentext, kein Fragenkatalog


# ------------------ Schritt 2: Befunde (Basis / optional erweitert) ------------------

def suggest_basic_exams_german(
    anamnese_filled: str,
    humanize: bool = True,
    phase: str = "initial"  # "initial" oder "persistent"
) -> str:
    """
    Liefert ein fertiges Feld "Befunde" (kurze Sätze/Telegraphiestil).
    'initial' = schlank/basisnah; 'persistent' = am Ende kurze Erweiterungen.
    """
    note = _swiss_style_note(humanize)

    sys_msg = (
        "Du bist erfahrener Hausarzt in einer Schweizer Hausarztpraxis.\n"
        + note + "\n"
        "Nur Untersuchungen, die in der Grundversorgung rasch verfügbar sind. "
        "Kein Overkill; dedupliziere gegen bereits erhobene Angaben.\n"
    ).strip()

    usr = {"anamnese_abgeschlossen": anamnese_filled, "phase": phase}

    prompt = (
        sys_msg
        + "\n\nVorgaben:\n"
        "- Zuerst Kurzstatus: AZ .\n"
        "- Dann fokussierte körperliche Untersuchung gemäss Leitsymptom\n"
        "- Optionale Basisgeraete/POCT: EKG, Lungenfunktion, Labor (3–6 relevante Parameter), Schellong.\n"
        "- Bei phase=\"persistent\": am Ende eine Zeile \"Bei Persistenz/Progredienz:\" mit 2–3 sinnvollen erweiterten Untersuchungen.\n"
        "- Keine Dopplungen zu bereits erwähnten/erhobenen Punkten.\n"
        "- Schweizer Standards.\n\n"
        "Antwort: Gib nur das Feld \"Befunde\" als zusammenhängenden, praxisnahen Text (keine JSON).\n"
    )

    return ask_openai(prompt + "\n\n" + json.dumps(usr, ensure_ascii=False))


# ------------------ Schritt 3: Beurteilung + Prozedere ------------------

def generate_assessment_and_plan_german(
    anamnese_final: str,
    befunde_final: str,
    humanize: bool = True,
    phase: str = "initial"
) -> Tuple[str, str]:
    """
    Erzeugt 'Beurteilung' (Arbeitsdiagnose + 2–3 DD) und 'Prozedere' (Praxisplan),
    dedupliziert, knapp, natürlich, Schweiz-Style.
    """
    try:
        red_flags_data = load_red_flags(RED_FLAGS_PATH)
        rf_hits = check_red_flags(anamnese_final + "\n" + befunde_final, red_flags_data, return_keywords=True) or []
        red_flags_list = [f"{kw} – {msg}" for (kw, msg) in rf_hits]
    except Exception:
        red_flags_list = []

    note = _swiss_style_note(humanize)

    sys_part = (
        "Du bist ein erfahrener Husarzt in einer Schweizer Hausarztpraxis.\n"
        + note + "\n"
        "Nur notwendige Infos; keine Wiederholungen von bereits Gesagtem. "
        "Schweizer/Europäische Guidelines priorisieren (danach UK/US). "
        "Kein ß, nur ss.\n"
    ).strip()

    usr = {"anamnese": anamnese_final, "befunde": befunde_final, "phase": phase, "red_flags": red_flags_list}

    prompt = (
        sys_part
        + "\n\nErzeuge zwei fertige Felder:\n\n"
        "Beurteilung:\n"
        "- Verdachtsdiagnose (kurz, plausibel aus Anamnese/Befunden) mit kurzer Begründung.\n"
        "- 2–3 DD (nur wenn klinisch sinnvoll), ohne Wiederholung von Befunden\n"
        "- Falls Red Flags vorhanden: kurze Einordnung dort, sonst weglassen\n\n"
        "Prozedere:\n"
        "- Unterpunkte, kein Fliesstext. Konkrete nächste Schritte in der Praxis (kurze, klare Bulletpoints)\n"
        "- Vorzeitige Wiedervorstellung\n"
        "- Verlauf/Kontrolle (realistisches Intervall)\n"
        "- Medikamentöse Massnahmen nur allgemein (keine erfundenen Dosierungen)\n"
        "- Bei \"persistent\": kurze Zeile zu weiterführender Abklärung/Überweisung\n\n"
        "Antwort: gib zuerst Beurteilung, dann eine Leerzeile, dann Prozedere.\n"
    )

    text = ask_openai(prompt + "\n\n" + json.dumps(usr, ensure_ascii=False))

    parts = [p.strip() for p in text.strip().split("\n\n", 1)]
    beurteilung = parts[0] if parts else ""
    prozedere = parts[1] if len(parts) > 1 else ""

    # Red Flags NICHT einbauen (werden im UI separat gezeigt)
    return beurteilung, prozedere


# ------------------ Ältere/zusätzliche Generatoren (optional nutzbar) ------------------

def generate_follow_up_questions(anamnese: str) -> str:
    prompt = (
        "Welche genau 5 anamnestischen Ergänzungen sind in der Hausarztpraxis besonders wichtig, "
        "um Diagnose/Schweregrad einzugrenzen?\n"
        + PROMPT_PREFIX
        + "\n\nVorgabe:\n"
        "- Stichpunkte, je 1 Zeile\n"
        "- Praxisrelevanz, keine Theorie\n"
        "- Nur Fragen/Aspekte, keine Diagnosen\n\n"
        "Anamnese:\n"
        + anamnese
        + "\n"
    )
    return ask_openai(prompt)


def generate_relevant_findings(anamnese: str) -> str:
    prompt = (
        "Welche klinischen Befunde/Untersuchungen (Status/Labor/POCT/evtl. Bildgebung) sind in der Hausarztpraxis "
        "besonders relevant, um Diagnose/Schweregrad einzugrenzen?\n"
        + PROMPT_PREFIX
        + "\n\nVorgabe:\n"
        "- Stichpunkte, je 1 Zeile\n"
        "- Max. 8 Punkte, priorisiert\n"
        "- Nur Untersuchungen/Befunde (keine Anamnese)\n\n"
        "Anamnese:\n"
        + anamnese
        + "\n"
    )
    return ask_openai(prompt)


def generate_differential_diagnoses(anamnese: str, befunde: str) -> str:
    prompt = (
        PROMPT_PREFIX
        + "\n\nEin Patient stellt sich mit folgender Anamnese vor:\n"
        + anamnese
        + "\n\nKlinische Befunde:\n"
        + befunde
        + "\n\nMache eine Liste mit mindestens 3 Differentialdiagnosen (DDs), sortiert nach Relevanz, "
        "mit jeweils einer kurzen Begründung.\n"
        "Antwortformat: Bulletpoints. Keine Fliesstexte.\n"
    )
    return ask_openai(prompt)


def generate_assessment_from_differential(selected_dds: str, anamnese: str, befunde: str) -> str:
    prompt = (
        PROMPT_PREFIX
        + "\n\nAnamnese:\n"
        + anamnese
        + "\n\nBefunde:\n"
        + befunde
        + "\n\nVom Arzt ausgewählte Differentialdiagnose(n):\n"
        + selected_dds
        + "\n\nFormuliere eine kurze, konzise ärztliche Beurteilung (ein paar sehr kurze Sätze), "
        "wie in einem hausärztlichen Verlaufseintrag.\n"
    )
    return ask_openai(prompt)


def generate_assessment(anamnese: str, befunde: str) -> str:
    prompt = (
        "Was ist die wahrscheinlichste Diagnose bzw. ärztliche Beurteilung?\n"
        + PROMPT_PREFIX
        + "\n\nAnamnese:\n"
        + anamnese
        + "\n\nBefunde:\n"
        + befunde
        + "\n\nAntworte in ein paar Sätzen (kurz, präzise).\n"
    )
    return ask_openai(prompt)


def generate_procedure(beurteilung: str, befunde: str, anamnese: str) -> str:
    # Red Flags separat (UI), hier nur Hinweis-Block zurückgeben, wenn gewünscht.
    try:
        red_flags_data = load_red_flags(RED_FLAGS_PATH)
        red_flags = check_red_flags(anamnese, red_flags_data, return_keywords=True)
    except Exception:
        red_flags = []

    red_flag_note = ""
    if red_flags:
        red_flag_note = "⚠️ Red Flags:\n" + "\n".join([f"- {keyword} – {message}" for keyword, message in red_flags]) + "\n\n"

    prompt = (
        PROMPT_PREFIX
        + "\n\nBeurteilung:\n"
        + beurteilung
        + "\n\nBefunde:\n"
        + befunde
        + "\n\nListe stichpunktartig ein empfohlenes Prozedere auf:\n"
        "- Abgemachte Massnahmen (inkl. Medikation, ohne erfundene Dosierungen)\n"
        "- Verlauf/Kontrollintervall\n"
        "- Vorzeitige Wiedervorstellung (konkrete Warnzeichen)\n"
        "- Weitere Abklärungen bei Ausbleiben der Besserung\n"
    )
    procedure = ask_openai(prompt)
    return red_flag_note + procedure
