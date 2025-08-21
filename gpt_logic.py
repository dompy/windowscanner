import os
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import tkinter as tk  # nur für Typverweise in Callbacks
from openai import OpenAI

# -----------------------------------------------------------------------------
# Konfiguration & Setup
# -----------------------------------------------------------------------------

MODEL_DEFAULT = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise EnvironmentError("❌ Umgebungsvariable OPENAI_API_KEY ist nicht gesetzt!")

client = OpenAI(api_key=api_key)

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
RED_FLAGS_PATH = os.path.join(THIS_DIR, "red_flags.json")

# Logging (praxisnah, schlank)
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Globaler Stil (kann in einzelnen Funktionen ergänzt werden)
PROMPT_PREFIX = (
    "Beziehe dich auf anerkannte medizinische Guidelines (Schweiz/Europa priorisiert; danach UK/US). "
    "Antworte bevorzugt stichpunktartig (ausser explizit Sätze gefordert), kurz, präzise, praxisnah (Schweiz). "
    "Schweizer Orthografie (ss statt ß). "
)

# Hybrid-Kompetenz: Hausarzt + Spezialwissen bei Bedarf (mehr Tiefe, aber praxisnah)
EXTRA_DEPTH_NOTE = (
    "Du bist ein erfahrener Hausarzt in der Schweiz. Ziehe bei Bedarf Spezialistenwissen (Innere Medizin, "
    "Infektiologie, Pneumologie, Kardiologie, Chirurgie usw.) hinzu, priorisiere aber stets die hausärztliche "
    "Praxisrelevanz. Geh eine Ebene tiefer, wo sinnvoll: \n"
    "- Bei Infekten/entzündlichen Zuständen: nenne den wahrscheinlichsten Erreger/Mechanismus.\n"
    "- Bei Differentialdiagnosen: gib für jede 1–2 Stichworte zur Begründung (klinisch/epidemiologisch/pathophysiologisch).\n"
    "- Bei Therapie: nenne Substanzklasse bzw. erstlinientaugliche Wirkstoffe (generisch), keine Dosierungen.\n"
)

# -----------------------------------------------------------------------------
# Red-Flags-Loader (optional)
# -----------------------------------------------------------------------------

def load_red_flags(path: str) -> Dict[str, Any]:
    try:
        from red_flags_checker import load_red_flags as _load
        return _load(path)
    except Exception:
        return {}


def check_red_flags(text: str, data: Dict[str, Any], return_keywords: bool = True):
    try:
        from red_flags_checker import check_red_flags as _check
        return _check(text, data, return_keywords=return_keywords)
    except Exception:
        return []


# -----------------------------------------------------------------------------
# Low-level OpenAI Helper
# -----------------------------------------------------------------------------

def ask_openai(prompt: str) -> str:
    """Einfacher Wrapper (Deutsch erzwungen, kurz & präzise)."""
    resp = client.chat.completions.create(
        model=MODEL_DEFAULT,
        messages=[
            {"role": "system", "content": "Antworte ausschliesslich auf Deutsch. Knapp, präzise, praxisnah."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    return resp.choices[0].message.content.strip()


def _ask_openai_json(
    messages: List[Dict[str, str]],
    model: str = MODEL_DEFAULT,
    temperature: float = 0.2,
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
        logger.warning("JSON-Parsing fehlgeschlagen – Rohtext zurückgegeben")
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


# -----------------------------------------------------------------------------
# 4 Felder – fix & fertig
# -----------------------------------------------------------------------------

def _format_full_entries_block(payload: Dict[str, Any]) -> str:
    """Kopierfertiger Block mit allen vier Feldern (Red Flags separat im UI)."""
    parts: List[str] = []
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
    context: Optional[Dict[str, Any]] = None,
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

    # System-Prompt
    sys_msg = (
        "Du bist ein erfahrener Hausarzt in einer Schweizer Hausarztpraxis.\n"
        + EXTRA_DEPTH_NOTE
        + "\n"
        "Ziel: Erzeuge vier dokumentationsfertige Felder (Deutsch), direkt kopierbar.\n"
        "WICHTIG:\n"
        "- Nichts erfinden. Wo Angaben fehlen: \"keine Angaben\", \"nicht erhoben\" oder \"noch ausstehend\".\n"
        "- Stil:\n"
        "  • Anamnese: kurz/telegraphisch; Dauer, Lokalisation/Qualität, relevante zu erfragende Begleitsymptome/Vorerkrankungen/Medikation auflisten, Kontext.\n"
        "  • Befunde: objektiv; Kurzstatus (AZ).\n"
        "  • Beurteilung: Verdachtsdiagnose inkl. typischer Erreger/Mechanismus (falls passend) + 2–4 DD mit je 1–2 Stichworten Begründung.\n"
        "  • Prozedere: kurze, klare Bulletpoints; nächste Schritte, Verlauf/Kontrolle, Vorzeitige Wiedervorstellung; Medikation nur allgemein (Substanzklasse/erstlinientaugliche Wirkstoffe), keine Dosierungen.\n"
        "- Schweizer/Europäische Guidelines priorisieren (danach UK/US).\n"
        "- Antworte ausschliesslich als JSON:\n\n"
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


# -----------------------------------------------------------------------------
# Schritt 1: Anamnese → Zusatzfragen (kurz)
# -----------------------------------------------------------------------------

def generate_anamnese_gaptext_german(
    anamnese_raw: str,
    answered_context: Optional[str] = "",
    humanize: bool = True,
) -> Tuple[Dict[str, Any], str]:
    """
    Erzeugt 2–5 gezielte Zusatzfragen basierend auf dem Patiententext.
    Return: (payload, fragen_text)
    payload: { "zusatzfragen": [..] }
    """

    def _sys_msg_base(note: str) -> str:
        return (
            "Du bist ein erfahrener Hausarzt in der Schweiz.\n"
            + EXTRA_DEPTH_NOTE
            + "\n"
            + note
            + "\n"
            "Aufgabe: Analysiere den Freitext des Patienten und formuliere 2–5 gezielte, medizinisch relevante Zusatzfragen, "
            "um die wahrscheinlichste Diagnose schnell einzugrenzen.\n"
            "Keine Untersuchungen nennen – nur Fragen.\n"
            "Fragen müssen kurz, klar und patientenverständlich formuliert sein.\n"
            "Antwort ausschliesslich als JSON im Format:\n"
            "{\n"
            "  \"zusatzfragen\": [\"Frage 1\", \"Frage 2\", \"Frage 3\", \"Frage 4\", \"Frage 5\"]\n"
            "}\n"
        ).strip()

    note = _swiss_style_note(humanize)
    sys_msg = _sys_msg_base(note)

    usr = {
        "eingabe_freitext": anamnese_raw,
        "bereits_beantwortet": answered_context or "",
        "hinweise": "Keine Untersuchungen, nur Zusatzfragen."
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


# -----------------------------------------------------------------------------
# Schritt 2: Befunde als Lückentext/Checkliste
# -----------------------------------------------------------------------------

def generate_befunde_gaptext_german(
    anamnese_filled: str,
    humanize: bool = True,
    phase: str = "initial",  # "initial" oder "persistent"
) -> Tuple[Dict[str, Any], str]:
    """
    Liefert praxisnahe Befunde als Lückentext/Checkliste zum direkten Ausfüllen
    (kein fertiger Status-Fliesstext). Return: (payload, befunde_lueckentext).
    payload: {"befunde_lueckentext": str, "befunde_checkliste": [..]}
    """
    note = _swiss_style_note(humanize)

    sys_msg = (
        "Du bist ein erfahrener Hausarzt in einer Schweizer Hausarztpraxis.\n"
        + EXTRA_DEPTH_NOTE
        + "\n"
        + note
        + "\n"
        "Aufgabe: Erzeuge eine Liste praxisrelevanter körperlicher Untersuchungen, die in der Hausarztpraxis zu erheben sind und "
        "zur Anamnese passen. Keine Vitalparameter!\n"
        "WICHTIG:\n"
        "- KEIN fertiger Statusbericht / kein Fliesstext.\n"
        "- Nur ausfüllbare Punkte mit Platzhaltern/Optionen.\n"
        "- Keine konkreten Messwerte eintragen, nur Struktur zum Ausfüllen.\n"
        "- Nichts doppeln, was in der Anamnese bereits beantwortet ist.\n"
        "- phase=\"initial\": nur Basics; phase=\"persistent\": am Ende eine Zusatzzeile mit 2–3 sinnvollen Erweiterungen.\n"
        "Antwort ausschliesslich als JSON:\n"
        "{\n"
        "  \"befunde_lueckentext\": \"string\",\n"
        "  \"befunde_checkliste\": [\"string\", \"...\"]\n"
        "}\n"
    ).strip()

    usr = {
        "anamnese_abgeschlossen": anamnese_filled,
        "phase": phase,
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

    # Fallback
    if not bef_text:
        lines = [
            "- AZ: gut / mittel / reduziert",
            "- fokussierte Untersuchung: __",
        ]
        if phase == "persistent":
            lines.append("Bei Persistenz/Progredienz: (Röntgen/US/erweitertes Labor) __")
        bef_text = "\n".join(lines)

    return result, bef_text


# -----------------------------------------------------------------------------
# Schritt 2b: Befunde – kurzer Freitext (für UI-Feld "Befunde")
# -----------------------------------------------------------------------------

def suggest_basic_exams_german(
    anamnese_filled: str,
    humanize: bool = True,
    phase: str = "initial",
) -> str:
    """
    Liefert ein fertiges Feld "Befunde" (kurze Sätze/Telegraphiestil).
    'initial' = schlank/basisnah; 'persistent' = am Ende kurze Erweiterungen.
    """
    note = _swiss_style_note(humanize)

    sys_msg = (
        "Du bist erfahrener Hausarzt in einer Schweizer Hausarztpraxis.\n"
        + EXTRA_DEPTH_NOTE
        + "\n"
        + note
        + "\n"
        "Nur Untersuchungen, die in der Grundversorgung rasch verfügbar sind. "
        "Kein Overkill; dedupliziere gegen bereits erhobene Angaben.\n"
        "Vorgaben:\n"
        "- Zuerst Kurzstatus: AZ .\n"
        "- Dann fokussierte körperliche Untersuchung gemäss Leitsymptom.\n"
        "- Optionale Basisgeraete/POCT: EKG, Lungenfunktion, Labor (nur 3–6 relevante Parameter), Schellong.\n"
        "- Bei phase=\"persistent\": am Ende eine Zeile \"Bei Persistenz/Progredienz:\" mit 2–3 sinnvollen erweiterten Untersuchungen.\n"
        "- Schweizer Standards.\n"
        "Antwort: Gib nur das Feld \"Befunde\" als zusammenhängenden, praxisnahen Text (keine JSON).\n"
    ).strip()

    usr = {"anamnese_abgeschlossen": anamnese_filled, "phase": phase}

    return ask_openai(sys_msg + "\n\n" + json.dumps(usr, ensure_ascii=False))


# -----------------------------------------------------------------------------
# Schritt 3: Beurteilung + Prozedere (tiefer, aber praxisnah)
# -----------------------------------------------------------------------------

def generate_assessment_and_plan_german(
    anamnese_final: str,
    befunde_final: str,
    humanize: bool = True,
    phase: str = "initial",
) -> Tuple[str, str]:
    """
    Erzeugt 'Beurteilung' (Arbeitsdiagnose + 2–3 DD mit Kurzbegründung inkl. Erreger/Mechanismus, wenn passend)
    und 'Prozedere' (Praxisplan, substanzklassenbasiert, keine Dosierungen).
    """
    try:
        red_flags_data = load_red_flags(RED_FLAGS_PATH)
        rf_hits = check_red_flags(anamnese_final + "\n" + befunde_final, red_flags_data, return_keywords=True) or []
        red_flags_list = [f"{kw} – {msg}" for (kw, msg) in rf_hits]
    except Exception:
        red_flags_list = []

    note = _swiss_style_note(humanize)

    sys_part = (
        "Du bist ein erfahrener Hausarzt in einer Schweizer Hausarztpraxis.\n"
        + EXTRA_DEPTH_NOTE
        + "\n"
        + note
        + "\n"
        "Nur notwendige Infos; keine Wiederholungen von bereits Gesagtem. "
        "Schweizer/Europäische Guidelines priorisieren (danach UK/US). Kein ß, nur ss.\n"
    ).strip()

    usr = {
        "anamnese": anamnese_final,
        "befunde": befunde_final,
        "phase": phase,
        "red_flags": red_flags_list,
    }

    prompt = (
        sys_part
        + "\n\nErzeuge zwei fertige Felder:\n\n"
        "Beurteilung:\n"
        "- Verdachtsdiagnose mit kurzer Begründung (inkl. typischer wahrscheinlicher Erreger/Mechanismus, falls passend).\n"
        "- 2–3 DD: jeweils mit 1–2 Stichworten zur Begründung (klinisch/epidemiologisch/pathophysiologisch).\n\n"
        "Prozedere:\n"
        "- Unterpunkte, kein Fliesstext. Konkrete nächste Schritte in der Praxis.\n"
        "- Medikamentöse Massnahmen: Substanzklasse/erstlinientaugliche Wirkstoffe (keine Dosierungen, keine Markennamen).\n"
        "- Vorzeitige Wiedervorstellung (Warnzeichen).\n"
        "- Verlauf/Kontrolle (realistisches Intervall).\n"
        "- Bei \"persistent\": kurze Zeile zu weiterführender Abklärung/Überweisung.\n\n"
        "Antwort: gib zuerst Beurteilung, dann eine Leerzeile, dann Prozedere.\n"
    )

    text = ask_openai(prompt + "\n\n" + json.dumps(usr, ensure_ascii=False))

    parts = [p.strip() for p in text.strip().split("\n\n", 1)]
    beurteilung = parts[0] if parts else ""
    prozedere = parts[1] if len(parts) > 1 else ""

    # Red Flags NICHT einbauen (werden im UI separat gezeigt)
    return beurteilung, prozedere


# -----------------------------------------------------------------------------
# Zusatz: Vignetten/MCQ-Analysator (direkte Antwort + Therapie)
# -----------------------------------------------------------------------------

def analyze_vignette_and_treatment(vignette_text: str) -> Dict[str, Any]:
    """
    Analysiert klinische Vignetten (inkl. MCQ/USMLE-ähnliche Texte) und liefert direkt die
    wahrscheinlichste Ursache/Diagnose sowie eine praxisrelevante Therapieempfehlung (ohne Dosierungen).

    Rückgabe-JSON (Beispiel):
    {
      "antwort_kurz": "Chlamydia psittaci (Psittakose, atypische Pneumonie) – erstlinientauglich: Doxycyclin (Tetrazykline)",
      "diagnose": "Atypische Pneumonie durch Chlamydia psittaci (Psittakose)",
      "wahrscheinlichster_erreger": "Chlamydia psittaci",
      "begruendung": "Vogelkontakt (Sittiche), Fieber, trockener Husten, LLL-Infiltrat, normale Na+, negative Influenza/SARS-CoV-2",
      "therapie_empfehlung": "Klasse: Tetrazykline; Wirkstoff: Doxycyclin (ohne Dosierung)",
      "leitlinienhinweis": "Empirische Erstlinientherapie gemäss CH/EU-Empfehlungen; stationäre Kriterien prüfen bei Hypoxie/Instabilität"
    }
    """
    sys_msg = (
        "Du bist ein erfahrener Hausarzt in der Schweiz mit Zugriff auf Spezialwissen (Infektiologie, Innere Medizin).\n"
        + EXTRA_DEPTH_NOTE
        + "\n"
        "Aufgabe: Analysiere die klinische Vignette und gib die wahrscheinlichste Ursache/Diagnose und eine geeignete, "
        "praxisrelevante Therapieempfehlung zurück. Antworte nicht als Frage, sondern als direkte Einschätzung. "
        "Keine Dosierungen, keine Markennamen. Schweizer/Europäische Guidelines priorisieren.\n"
        "Antworte ausschliesslich als JSON mit folgenden Feldern:\n"
        "{\n"
        "  \"antwort_kurz\": \"string\",\n"
        "  \"diagnose\": \"string\",\n"
        "  \"wahrscheinlichster_erreger\": \"string\",\n"
        "  \"begruendung\": \"string\",\n"
        "  \"therapie_empfehlung\": \"string\",\n"
        "  \"leitlinienhinweis\": \"string\"\n"
        "}\n"
    ).strip()

    usr = {
        "vignette": vignette_text,
        "hinweise": "Falls MCQ-Optionen vorhanden sind: ignoriere Buchstaben A–D und gib die beste Lösung direkt aus."
    }

    result = _ask_openai_json(
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": json.dumps(usr, ensure_ascii=False)},
        ]
    )

    # Minimaler Sanity-Check
    required = [
        "antwort_kurz",
        "diagnose",
        "wahrscheinlichster_erreger",
        "begruendung",
        "therapie_empfehlung",
        "leitlinienhinweis",
    ]
    for k in required:
        result.setdefault(k, "noch ausstehend")

    return result


# -----------------------------------------------------------------------------
# Ältere/zusätzliche Generatoren (weiter nutzbar)
# -----------------------------------------------------------------------------

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
        "- Abgemachte Massnahmen (inkl. Medikation als Substanzklasse/erstlinientaugliche Wirkstoffe; keine Dosierungen)\n"
        "- Verlauf/Kontrollintervall\n"
        "- Vorzeitige Wiedervorstellung (konkrete Warnzeichen)\n"
        "- Weitere Abklärungen bei Ausbleiben der Besserung\n"
    )
    procedure = ask_openai(prompt)
    return red_flag_note + procedure


# -----------------------------------------------------------------------------
# UI-Helfer (nur verwendet, wenn dieses Modul direkt importiert wird)
# -----------------------------------------------------------------------------

class _UiStubs:
    """Nur Typhinweise für UI-Integration (tkinter-Callbacks)."""
    fields: Dict[str, tk.Text]
    txt_gap: tk.Text


__all__ = [
    "generate_full_entries_german",
    "generate_anamnese_gaptext_german",
    "generate_befunde_gaptext_german",
    "suggest_basic_exams_german",
    "generate_assessment_and_plan_german",
    "generate_follow_up_questions",
    "generate_relevant_findings",
    "generate_differential_diagnoses",
    "generate_assessment_from_differential",
    "generate_assessment",
    "generate_procedure",
    "analyze_vignette_and_treatment",
]