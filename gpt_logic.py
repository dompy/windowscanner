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
DEBUG_PROMPTS = os.getenv("DEBUG_PROMPTS", "0").lower() in {"1","true","yes","on"}
logging.basicConfig(level=logging.DEBUG if DEBUG_PROMPTS else logging.INFO,
                    format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Globaler Stil (kann in einzelnen Funktionen ergänzt werden)
PROMPT_PREFIX = (
    "Beziehe dich auf anerkannte medizinische Guidelines (Schweiz/Europa priorisiert; danach UK/US). "
    "Antworte bevorzugt stichpunktartig (ausser explizit Sätze gefordert), präzise, kurz, praxisnah und exakt (Schweiz). "
    "Schweizer Orthografie (ss statt ß). "
)

# Hybrid-Kompetenz: Hausarzt + Spezialwissen bei Bedarf (mehr Tiefe, aber praxisnah)
EXTRA_DEPTH_NOTE = (
    "Du bist ein erfahrener Hausarzt in der Schweiz. Ziehe bei Bedarf vertieftes Spezialistenwissen (Innere Medizin, "
    "Infektiologie, Pneumologie, Kardiologie, Nephrologie, Hepatologie, Gynäkologie, Urologie, Endokrinologie, Chirurgie, Kardiologie, Onkologie, Psychiatrie usw.) hinzu, priorisiere aber stets die hausärztliche "
    "Praxisrelevanz. Geh noch eine Ebene tiefer, wo möglich:\n"
    "- Bei Infekten/entzündlichen Zuständen: nenne den wahrscheinlichsten Erreger/Mechanismus.\n"
    "- Bei Differentialdiagnosen: gib für jede 1–2 Stichworte zur Begründung (klinisch/epidemiologisch/pathophysiologisch).\n"
    "- IMMER Medikationsabgleich (Medication Reconciliation): erkenne bestehende Wirkstoffe/Klassen, vermeide "
    "'Start'-Empfehlungen für bereits laufende Therapien; formuliere stattdessen klare Anpassungen "
    "(Weiterführen vs. Dosisreduktion/Stop/Wechsel) gemäss Leitlinien und klinischem Kontext.\n"
    "- Therapie: Medikamente, Substanzklasse bzw. erstlinientaugliche Wirkstoffe (generisch), keine Dosierungen.\n"
)

THERAPY_DECISION_NOTE = (
    "Therapie-Selektion strikt kontextabhängig:\n"
    "- Prüfe Indikation, Schweregrad, Ziel der Behandlung (kurativ vs. palliativ), Patientenvorzug.\n"
    "- Beziehe Performance-Status (z. B. ECOG aus Text ableitbar: bettlägerig/cachexie → schlechtes PS) und Organfunktion "
    "(z. B. Bilirubin/Kreatinin) ein.\n"
    "- Wenn Leitlinien klar einen einzelnen Erstlinien-Wirkstoff/eine Regimenbezeichnung favorisieren: nenne genau diesen "
    "(generischer Name, keine Dosierung/Marke).\n"
    "- Wenn aufgrund schlechten PS/Komorbiditäten die Nutzen-Schaden-Bilanz gegen systemische Therapie spricht: "
    "priorisiere 'Best Supportive Care/Hospiz' statt Chemotherapie.\n"
    "- Medication Reconciliation: starte keine Klasse doppelt; formuliere ggf. Anpassung/Deeskalation.\n"
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
        + THERAPY_DECISION_NOTE 
        + "Beziehe ausdrücklich die bestehende Medikation aus dem Eingabetext ein (Medication Reconciliation). "
        "Gib KEINE Start-/Eskalations-Empfehlung für eine bereits laufende Wirkstoffklasse ohne klare Begründung; "
        "bei geeigneten Fällen formuliere Deeskalation (z. B. Steroiddosis reduzieren/absetzen) statt Eskalation.\n"
        + "\n"
        "Ziel: Erzeuge vier dokumentationsfertige Felder (Deutsch), direkt kopierbar.\n"
        "WICHTIG:\n"
        "- Nichts erfinden. Wo Angaben fehlen: \"keine Angaben\", \"nicht erhoben\" oder \"noch ausstehend\".\n"
        "- Stil:\n"
        "  • Anamnese: kurz/telegraphisch; Dauer, Lokalisation/Qualität, relevante zu erfragende Begleitsymptome/Vorerkrankungen/Medikation auflisten, Kontext.\n"
        "  • Befunde: objektiv; Kurzstatus (AZ).\n"
        "  • Beurteilung: Verdachtsdiagnose inkl. typischer Erreger/Mechanismus (falls passend) + 2–4 DD mit je 1–2 Stichworten Begründung.\n"
        "  • Prozedere: kurze, klare Bulletpoints; nächste Schritte, Verlauf/Kontrolle, Vorzeitige Wiedervorstellung; Medikation mit Substanzklasse und Beispielen lokal eingesetzter Pharmaka), keine Dosierungen.\n"
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
            + THERAPY_DECISION_NOTE 
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
        + THERAPY_DECISION_NOTE 
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
        + THERAPY_DECISION_NOTE 
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
    agent_mode: bool = True
) -> Tuple[str, str]:
    """
    Erzeugt 'Beurteilung' (Arbeitsdiagnose + 2–3 DD mit Kurzbegründung inkl. Erreger/Mechanismus, wenn passend)
    und 'Prozedere' (Praxisplan, substanzklassenbasiert mit Beispielen lokal eingesetzter Pharmaka, keine Dosierungen).
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
        + THERAPY_DECISION_NOTE 
        + "\n"
        + note
        + "\n"
        "Nur notwendige Infos; keine Wiederholungen von bereits Gesagtem. "
        "Schweizer/Europäische Guidelines priorisieren (danach UK/US). Kein ß, nur ss.\n"
        "Berücksichtige die bestehende Medikation (Medication Reconciliation). "
        "Keine 'Start'-Therapie für eine bereits laufende Klasse; stattdessen klare Anpassung "
        "(Weiterführen vs. Reduktion/Stop/Wechsel) gemäss Leitlinien und Befundlage.\n"
    ).strip()

    usr = {
        "anamnese": anamnese_final,
        "befunde": befunde_final,
        "phase": phase,
        "red_flags": red_flags_list,
    }

    agent_line = (
        "- Medikamentöse Massnahmen: falls indiziert, mindestens EIN erstlinientauglicher generischer Wirkstoff/Regimenname "
        "(ohne Dosierung/Marke). "
        if agent_mode
        else "- Medikamentöse Massnahmen: Substanzklasse/erstlinientaugliche Wirkstoffe (ohne Dosierung/Marke). "
    )

    prompt = (
        sys_part
        + "\n\nErzeuge zwei fertige Felder:\n\n"
        "Beurteilung:\n"
        "- Verdachtsdiagnose mit kurzer Begründung (inkl. typischer wahrscheinlicher Erreger/Mechanismus, falls passend).\n"
        "- 2–3 DD: jeweils mit 1–2 Stichworten zur Begründung (klinisch/epidemiologisch/pathophysiologisch).\n\n"
        "Prozedere:\n"
        "- Unterpunkte, kein Fliesstext. Konkrete nächste Schritte in der Praxis.\n"
        + agent_line
        + "Wenn aufgrund PS/Komorbiditäten kontraindiziert: 'Best Supportive Care/Hospiz' klar priorisieren. "
        "Keine Alternativliste.\n"
        "Falls gleiche Klasse bereits läuft: klar 'Weiterführen' vs. 'Dosisreduktion/Stop/Wechsel' formulieren.\n"
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

def _ensure_management_hint(text: str) -> tuple[str, bool]:
    """
    Hängt – falls nicht vorhanden – den MCQ-Management-Hint an.
    Rückgabe: (finaler_text, hint_wurde_angehaengt)
    """
    core = "which is the most appropriate management"
    if core in text.lower():
        return text, False
    new_text = (text.rstrip() + "\n\nWhich is the most appropriate management?").strip()
    return new_text, True



def analyze_vignette_and_treatment(vignette_text: str) -> Dict[str, Any]:
    """
    Analysiert klinische Vignetten/MCQs und liefert:
      - wahrscheinlichste Diagnose/Ursache,
      - mindestens EIN erstlinientaugliches Medikament/Regimen (oder 'Best Supportive Care/Hospiz'),
      - Begründung.
    Keine Dosierungen/Markennamen. CH/EU-Guidelines priorisieren.
    """
    DECISION_POLICY = (
        "Entscheidungs-Policy:\n"
        "- Gib mindestens EINEN Vorschlag aus: entweder 1 generischer Wirkstoff/Regimenname ODER 'Best Supportive Care/Hospiz'.\n"
        "- Wähle anhand Indikation, Performance-Status (aus Text ableitbar), Organfunktion (Labore) und Patientenziel.\n"
        "- Medication Reconciliation: Starte keine Klasse, die bereits läuft; formuliere stattdessen Anpassung/Deeskalation, falls passend.\n"
    )

    KNOWLEDGE_ANCHORS = (
        "Knowledge-Anker (hochgewichtet, kurz):\n"
        "- Lithium-assoziierte Polyurie/Polydipsie, hohe Urinmenge, niedrige Urin-Osmolalität: nephrogener DI → Amilorid (ENaC-Blocker) bevorzugt; "
        "Erwägung Lithium-Reduktion/Stop je nach Psychiatrie.\n"
        "- Psittakose (Vogelkontakt, atyp. Pneumonie): Doxycyclin (Tetrazyklin).\n"
        "- Kontaktlinsen-Keratitis mit eitrigem Sekret: antipseudomonale topische Fluorchinolone.\n"
        "- Ältere, gebrechliche, metastasiertes Pankreas-CA, schlechtes PS/Cholestase: häufig 'Best Supportive Care/Hospiz' statt Chemo.\n"
    )

    sys_msg = (
        "Du bist ein erfahrener Hausarzt in der Schweiz mit Zugriff auf Spezialwissen (Innere Medizin, Infektiologie, Onkologie, Nephrologie).\n"
        + EXTRA_DEPTH_NOTE
        + DECISION_POLICY
        + KNOWLEDGE_ANCHORS
        + "Berücksichtige explizit bestehende Medikamente im Text (Medication Reconciliation) und vermeide Dopplungen derselben Klasse; "
          "formuliere nach Möglichkeit Anpassung/Deeskalation statt erneuter Start.\n"
        "Aufgabe: Analysiere die Vignette und gib die wahrscheinlichste Ursache/Diagnose und eine geeignete, praxisrelevante "
        "Therapie zurück. Antworte nicht als Frage, sondern als direkte Einschätzung. Keine Dosierungen, keine Markennamen. "
        "Schweizer/Europäische Guidelines priorisieren.\n"
        "Antworte ausschliesslich als JSON mit folgenden Feldern:\n"
        "{\n"
        "  \"antwort_kurz\": \"string\",            \n"
        "  \"diagnose\": \"string\",                 \n"
        "  \"wahrscheinlichster_erreger\": \"string\",\n"
        "  \"begruendung\": \"string\",              \n"
        "  \"therapie_empfehlung\": \"string\",      \n"
        "  \"leitlinienhinweis\": \"string\"         \n"
        "}\n"
        "WICHTIG: 'therapie_empfehlung' enthält mindestens EINEN generischen Wirkstoff/Regimen-Namen ODER 'Best Supportive Care/Hospiz'.\n"
    ).strip()

    # Ein kurzes Few‑Shot, das Lithium‑DI → Amilorid erzwingt (knapp halten, damit Kontext klein bleibt)
    fewshot_user = {
        "vignette": (
            "32-year-old with bipolar disorder recently started lithium; now polyuria/polydipsia. "
            "Vitals normal. Labs: low urine osmolality, high urine volume, serum Na 145. Other meds: quetiapine."
        ),
        "hinweise": "MCQ-Optionen ignorieren; beste Lösung direkt."
    }
    fewshot_assistant = {
        "antwort_kurz": "Nephrogener Diabetes insipidus (lithiumbedingt) – Amilorid (ENaC-Blocker).",
        "diagnose": "Nephrogener DI durch Lithium",
        "wahrscheinlichster_erreger": "keine (nicht-infektiös)",
        "begruendung": "Lithium-Exposition; hohe Urinmenge; niedrige Urin-Osmolalität; Polyurie/Polydipsie.",
        "therapie_empfehlung": "Amilorid",
        "leitlinienhinweis": "Diuretika-Kombinationen nur fallweise; Lithium-Anpassung interdisziplinär prüfen."
    }

    vignette_final, hint_added = _ensure_management_hint(vignette_text)

    usr = {
        "vignette": vignette_final,
        "hinweise": "Falls MCQ-Optionen vorhanden sind: ignoriere Buchstaben A–D und gib die beste Lösung direkt aus."
    }

    # Debug-Ausgaben
    if DEBUG_PROMPTS:
        logger.info("Mgmt-Hint: %s", "ANGEHÄNGT" if hint_added else "bereits vorhanden")
        logger.debug("VIGNETTE_FINAL (%d Zeichen):\n%s", len(vignette_final), vignette_final)

    msgs = [
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": json.dumps(fewshot_user, ensure_ascii=False)},
        {"role": "assistant", "content": json.dumps(fewshot_assistant, ensure_ascii=False)},
        {"role": "user", "content": json.dumps(usr, ensure_ascii=False)},
    ]

    if DEBUG_PROMPTS:
        debug_path = os.path.join(THIS_DIR, "debug_last_prompt.json")
        with open(debug_path, "w", encoding="utf-8") as f:
            json.dump(msgs, f, ensure_ascii=False, indent=2)
        logger.info("📝 Prompt-Dump gespeichert: %s", debug_path)

    result = _ask_openai_json(messages=msgs)

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

    # Durchsetzen „genau 1 Vorschlag“ (kein Komma/Semikolon mit Alternativen)
    if isinstance(result.get("therapie_empfehlung"), str):
        clean = result["therapie_empfehlung"].strip()
        # einfache Normalisierung
        for sep in [";", "/", ","]:
            if sep in clean and "Best Supportive Care" not in clean and "Hospiz" not in clean:
                clean = clean.split(sep)[0].strip()
        result["therapie_empfehlung"] = clean

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
        "- Abgemachte Massnahmen (inkl. Medikation mit Substanzklasse und Beispielen lokal eingesetzter Pharmaka)\n"
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
