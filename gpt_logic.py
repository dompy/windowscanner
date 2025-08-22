# gpt_logic.py
"""
Psychologie-fokussierte Logik für den Praxis-Assistenten
- Saubere Trennung Logik/UI (keine tkinter-Referenzen)
- Zentraler Resolver für Red-Flag-Dateien (Psychologie bevorzugt)
- Schweizer Orthografie, telegraphischer Stil
- Funktionen, die vom UI genutzt werden:
    * resolve_red_flags_path()
    * generate_full_entries_german()
    * generate_anamnese_gaptext_german()
    * generate_status_gaptext_german()
    * generate_assessment_and_plan_german()
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple
from openai import OpenAI
from red_flags_checker import check_red_flags, load_red_flags


# ------------------ Logging ------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------ Config & Client ------------------
MODEL_DEFAULT = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

from typing import Optional
from openai import OpenAI

_client: Optional[OpenAI] = None

def _get_openai_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise EnvironmentError("❌ Umgebungsvariable OPENAI_API_KEY ist nicht gesetzt!")
        _client = OpenAI(api_key=api_key)
    return _client

def reset_openai_client() -> None:
    """Vergisst den gecachten Client. Beim nächsten Call wird aus der aktuellen ENV neu initialisiert."""
    global _client
    _client = None  


# ------------------ Pfade & Resolver ------------------
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
RED_FLAGS_PATH = os.path.join(THIS_DIR, "red_flags.json")
PSYCH_RED_FLAGS_PATH = os.path.join(THIS_DIR, "psych_red_flags.json")

APP_MODE = os.getenv("APP_MODE", "psychology").lower()  # optionaler Modus-Schalter


def resolve_red_flags_path(prefer_psych: bool = True) -> str:
    """Bevorzugt psychologische Red-Flags, fällt ansonsten auf medizinische zurück.

    prefer_psych=True -> zuerst psych_red_flags.json, dann red_flags.json
    Bei fehlenden Dateien wird RED_FLAGS_PATH zurückgegeben (Caller soll Exceptions abfangen).
    """
    # Modus überschreibt Flag
    prefer_psych = True if APP_MODE == "psychology" else prefer_psych
    try:
        if prefer_psych and os.path.exists(PSYCH_RED_FLAGS_PATH):
            return PSYCH_RED_FLAGS_PATH
        if os.path.exists(RED_FLAGS_PATH):
            return RED_FLAGS_PATH
    except Exception:
        pass
    return RED_FLAGS_PATH


# ------------------ Helper ------------------

def ask_openai(prompt: str) -> str:
    resp = _get_openai_client().chat.completions.create(
        model=MODEL_DEFAULT,
        messages=[
            {"role": "system", "content": "Antworte ausschliesslich auf Deutsch. Umfassend, präzise, praxisnah."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    return (resp.choices[0].message.content or "").strip()

def _ask_openai_json(*, messages: List[Dict[str, str]], model: str = MODEL_DEFAULT, temperature: float = 0.2) -> Dict[str, Any]:
    resp = _get_openai_client().chat.completions.create(
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
        "Natürlich klingend wie klinische KG-Einträge, kurz/telegraphisch; "
    )
    if humanize:
        base += "gelegentlich minimale Tippfehler/Verkürzungen einbauen; "
    base += "keine Floskeln, keine Romane."
    return base

def _fallback_format_anamnese_local(freetext: str, gaptext: str) -> str:
    """Sehr einfacher Fallback ohne LLM: Bullet-Zeilen glätten."""
    import re
    parts = []
    ft = (freetext or "").strip()
    if ft:
        # Kleiner Happen: "gestern geweint" -> "Patient berichtet, gestern geweint zu haben."
        if not ft.endswith((".", "!", "?")):
            ft = ft.rstrip() + "."
        parts.append(ft)

    for raw in (gaptext or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        # "– " oder "- " entfernen
        line = re.sub(r"^\s*[–-]\s*", "", line)
        # Endungen wie "? ja"/"? nein" in Klammern
        line = re.sub(r"\?\s*ja\s*$", " (bejaht).", line)
        line = re.sub(r"\?\s*nein\s*$", " (verneint).", line)
        # Falls noch kein Satzende: Punkt
        if not line.endswith((".", "!", "?")):
            line += "."
        parts.append(line)
    return " ".join(parts).strip()


def format_anamnese_fliess_text(freetext: str, gaptext: str) -> str:
    """
    Formatiert Freitext + 'Erweiterte Anamnese' (Bullet-Liste) zu EINEM
    klinischen Kurzabsatz (Schweizer Orthografie, telegraphisch, aber fliessend).
    """
    # 1) Versuch: LLM
    try:
        sys_msg = (
            "Du bist erfahrener Psychologe in einer Schweizer Praxis. "
            "Formuliere aus a) einer kurzen freien Anamnese und b) einer Liste mit Zusatzfragen/-antworten "
            "(Zeilen beginnen evtl. mit '-' und enden evtl. mit 'ja/nein') EINEN kompakten Fliesstextabsatz. "
            "Nutze Schweizer Orthografie (ss statt ß), telegraphisch aber gut lesbar. "
            "Keine Aufzählungspunkte, keine Frageform, keine Diagnosen. "
            "Beziehe Antworten 'ja/nein' als bejaht/verneint ein. Beginne typisch mit 'Patient berichtet, …' "
            "oder setze Angaben natürlich zusammen."
        ).strip()
        usr = {
            "freitext": freetext or "",
            "zusatzfragen_liste": gaptext or "",
            "hinweis": "Nur EIN Absatz. Kein 'Anamnese:' davor. Keine Bulletpoints."
        }
        text = ask_openai(
            sys_msg + "\n\nDaten:\n" + json.dumps(usr, ensure_ascii=False)
        )
        text = (text or "").strip()
        if text:
            # Einzeilig machen (falls Modell Absatzumbrüche setzt)
            return " ".join(x.strip() for x in text.splitlines() if x.strip())
    except Exception:
        pass

    # 2) Fallback ohne LLM
    return _fallback_format_anamnese_local(freetext, gaptext)


def _format_full_entries_block(payload: Dict[str, Any]) -> str:
    """Kopierfertiger Block mit allen vier Feldern (Red Flags separat im UI)."""
    parts: List[str] = []
    parts.append("Anamnese:")
    parts.append((payload.get("anamnese_text") or "keine Angaben").strip())
    parts.append("")
    parts.append("Status:")
    parts.append((payload.get("status_text") or "keine Angaben").strip())
    parts.append("")
    parts.append("Beurteilung:")
    parts.append((payload.get("beurteilung_text") or "keine Angaben").strip())
    parts.append("")
    parts.append("Prozedere:")
    parts.append((payload.get("prozedere_text") or "keine Angaben").strip())
    return "\n".join(parts).strip()


# ------------------ 4 Felder – Psychologie ------------------

def generate_full_entries_german(
    user_input: str, context: Optional[Dict[str, Any]] = None
) -> Tuple[Dict[str, str], str]:
    """Erzeugt vier dokumentationsfertige Felder (Anamnese/Psychopathologischer Befund/Einschätzung/Prozedere).

    Gibt zusätzlich (falls vorhanden) eine Liste "red_flags" im Payload zurück.
    """
    context = context or {}

    # Red Flags lokal prüfen (separat im UI anzeigen)
    try:
        path = resolve_red_flags_path(prefer_psych=True)
        red_flags_data = load_red_flags(path)
        rf_hits = check_red_flags(user_input, red_flags_data, return_keywords=True) or []
        red_flags_list = [f"{kw} – {msg}" for (kw, msg) in rf_hits]
    except Exception:
        red_flags_list = []

    sys_msg = (
        "Du bist erfahrener Psychologe in einer Schweizer Praxis (ambulante Erstkonsultation).\n"
        "Ziel: Erzeuge vier dokumentationsfertige Felder (Deutsch), direkt kopierbar.\n"
        "WICHTIG:\n"
        "- Nichts erfinden. Wo Angaben fehlen: \"keine Angaben\", \"nicht erhoben\" oder \"noch ausstehend\".\n"
        "- Inhalte & Stil (telegraphisch, umfassend, praxisnah; Schweizer Orthografie, kein ß, kein z.B.):\n"
        "  • Anamnese: Hauptanliegen, Beginn/Verlauf, Auslöser/Belastung, Ressourcen/Schutzfaktoren, Vorerfahrungen, relevante somatische/psychiatrische Vorerkrankungen & Medikation nur nennen, Substanzkonsum, Kontext (Arbeit/Beziehung/Soziales).\n"
        "  • Status: psychopathologischer Status (Erscheinung/Verhalten, Stimmung/Affekt, Antrieb, Denken/Inhalt, Wahrnehmung, Orientierung/Kognition, Insight), Risikoabschätzung (Suizidalität/Fremdgefährdung: ja/nein/unklar, Schutzfaktoren), Funktionsniveau kurz. Keine körperlichen Untersuchungen.\n"
        "  • Beurteilung: psychologische Einschätzung/Hypothesen (umfassend, plausibel), 2–4 Alternativhypothesen/Komorbiditätserwägungen, Schweregrad/Dringlichkeit.\n"
        "  • Prozedere: klare Bulletpoints (Interventionen, Hausaufgaben, Sicherheit/Krisenplan, Einbezug Dritter nach Einwilligung, Verlauf/Kontrolle, Warnzeichen). Medikation nur als Koordination mit ärztlichen Stellen, keine Dosierungen.\n"
        "- Antworte ausschliesslich als JSON:\n\n"
        "{\n"
        "  \"anamnese_text\": \"string\",\n"
        "  \"status_text\": \"string\",\n"
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

    if red_flags_list:
        result["red_flags"] = red_flags_list

    full_block = _format_full_entries_block(result)
    return result, full_block


# ------------------ Anamnese → Zusatzfragen ------------------

def generate_anamnese_gaptext_german(
    anamnese_raw: str,
    answered_context: Optional[str] = "",
    humanize: bool = True,
) -> Tuple[Dict[str, Any], str]:
    """Erzeugt 2–5 gezielte, psychologisch relevante Zusatzfragen.

    Return: (payload, fragen_text) — payload: {"zusatzfragen": [...]}
    """

    def _sys_msg_base(note: str) -> str:
        return (
            "Du bist erfahrener Psychologe in einer Schweizer Praxis.\n"
            + note
            + "\n"
            + "Aufgabe: Analysiere den Freitext und formuliere **2–5 gezielte, psychologisch relevante Zusatzfragen**, "
            + "um Anliegen, Schweregrad und Dringlichkeit einzugrenzen.\n"
            + "Fokus: Beginn/Verlauf; Auslöser/Belastung; Ressourcen/Schutzfaktoren; Funktionsniveau (Arbeit/Beziehung/Alltag); "
            + "Substanzkonsum; bisherige Behandlungen/Hilfen; **Risiko** (Suizidalität/Fremdgefährdung: ja/nein/unklar, Schutzfaktoren).\n"
            + "WICHTIG: keine Diagnosen, keine Untersuchungen/Testlisten — nur kurze, patientenverständliche Fragen.\n"
            + "Vermeide Redundanz (siehe bereits beantwortete Punkte).\n"
            + "Antworte ausschliesslich als JSON:\n"
            + "{\n  \"zusatzfragen\": [\"Frage 1\", \"Frage 2\", \"Frage 3\", \"Frage 4\", \"Frage 5\"]\n}\n"
            + "Die Liste darf 2–5 Einträge enthalten."
        ).strip()

    note = _swiss_style_note(humanize)
    sys_msg = _sys_msg_base(note)

    usr = {
        "eingabe_freitext": anamnese_raw,
        "bereits_beantwortet": answered_context or "",
        "hinweise": (
            "Keine Untersuchungen/Testbatterien. Nur Fragen, kurz und klar. "
            "Priorisiere Risikoabschätzung und nächste sinnvolle Klärungsschritte."
        ),
    }

    result = _ask_openai_json(
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": json.dumps(usr, ensure_ascii=False)},
        ]
    )

    # Nachbearbeitung: säubern, deduplizieren, Begrenzung 2–5, Fallback
    def _clean_list(xs: List[str]) -> List[str]:
        seen: set[str] = set()
        out: List[str] = []
        for x in xs or []:
            q = (x or "").strip()
            if not q or q in seen:
                continue
            seen.add(q)
            out.append(q)
        return out

    fragen_liste = _clean_list(result.get("zusatzfragen", []) if isinstance(result, dict) else [])

    if len(fragen_liste) > 5:
        fragen_liste = fragen_liste[:5]

    if len(fragen_liste) < 2:
        fallback = [
            "Seit wann bestehen die Beschwerden und wie haben sie sich entwickelt?",
            "Welche Situationen oder Gedanken verschlimmern bzw. bessern die Symptome?",
            "Gibt es aktuell Suizidgedanken oder Gedanken, jemandem zu schaden?",
            "Wie stark beeinträchtigen die Beschwerden Ihren Alltag (Arbeit, Beziehungen, Schlaf)?",
            "Welche bisherigen Hilfen/Strategien haben etwas genutzt?",
        ]
        for q in fallback:
            if len(fragen_liste) >= 2:
                break
            if q not in fragen_liste:
                fragen_liste.append(q)

    result = {"zusatzfragen": fragen_liste}
    fragen_text = "\n".join(f"- {f}" for f in fragen_liste) if fragen_liste else anamnese_raw
    return result, fragen_text


# ------------------ Psychologischer Befund (Lückentext) ------------------

def generate_status_gaptext_german(
    anamnese_filled: str,
    humanize: bool = True,
    phase: str = "initial",  # bleibt für API-Kompatibilität erhalten
) -> Tuple[Dict[str, Any], str]:
    """Liefert psychologischen Status/Exploration als Lückentext/Checkliste (kein Fliesstext)."""
    note = _swiss_style_note(humanize)

    sys_msg = (
        "Du bist erfahrener Psychologe in einer Schweizer Praxis.\n"
        + note
        + "\n"
        + "Aufgabe: Erzeuge eine strukturierte Liste **psychologischer Status/Explorationspunkte** passend zur Anamnese. "
        + "Kein Fliesstext, **keine körperlichen Untersuchungen**, keine Diagnosen.\n"
        + "WICHTIG:\n"
        + "- KEIN fertiger Statusbericht; nur ausfüllbare Punkte mit Platzhaltern/Optionen.\n"
        + "- Keine konkreten Mess-/Skalenwerte eintragen; nur Struktur zum Ausfüllen.\n"
        + "- Nichts doppeln, was in der Anamnese bereits beantwortet ist.\n"
        + "Bevorzugte Bereiche (anpassen je nach Fall): Erscheinung/Verhalten; Stimmung/Affekt; Antrieb/Psychomotorik; "
        + "Denken (Form/Inhalt: Grübeln/Zwang/überwertige Ideen); Wahrnehmung; Kognition/Orientierung/Aufmerksamkeit; Insight/Motivation; "
        + "Substanzkonsum (Art/Menge/Frequenz); Funktionsniveau (Arbeit/Beziehung/Schlaf/Alltag); Ressourcen/Schutzfaktoren; **Risikoabschätzung** (Suizidalität/Fremdgefährdung: ja/nein/unklar, Schutzfaktoren).\n"
        + "Antworte ausschliesslich als JSON:\n{\n  \"status_lueckentext\": \"string\",\n  \"status_checkliste\": [\"string\", \"...\"]\n}\n"
    ).strip()

    usr = {"anamnese_abgeschlossen": anamnese_filled, "phase": phase}

    result = _ask_openai_json(
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": json.dumps(usr, ensure_ascii=False)},
        ]
    )

    bef_text = ""
    if isinstance(result, dict):
        bef_text = (result.get("status_lueckentext") or "").strip()

    # Fallback, falls das Modell nichts liefert
    if not bef_text:
        lines = [
            "- Erscheinung/Verhalten: gepflegt / ungepflegt / auffällig; Kontakt: gut / distanziert / vermeidend",
            "- Stimmung/Affekt: euthym / gedrückt / ängstlich; Affekt: stabil / labil / verflacht",
            "- Antrieb/Psychomotorik: normal / vermindert / gesteigert",
            "- Denken – Form/Inhalt: geordnet / Umständlichkeit / Grübeln / Zwangsgedanken; Fremd-/Selbstabwertung: ja/nein",
            "- Wahrnehmung: Halluzinationen/Entfremdung: ja/nein/unklar",
            "- Kognition/Orientierung/Aufmerksamkeit: intakt / leichte Defizite (kurz angeben: __ )",
            "- Insight/Motivation: gut / eingeschränkt; Ziele/Erwartungen: __",
            "- Substanzkonsum: Art/Menge/Frequenz: __ ; zuletzt: __",
            "- Funktionsniveau: Arbeit/Schule: __ ; Beziehungen: __ ; Schlaf: __ ; Alltagsbewältigung: __",
            "- Ressourcen/Schutzfaktoren: __",
            "- Risikoabschätzung: Suizidalität: ja/nein/unklar (Akutplan? __); Fremdgefährdung: ja/nein/unklar; Schutzfaktoren: __",
        ]
        bef_text = "\n".join(lines)
        result = {
            "status_lueckentext": bef_text,
            "status_checkliste": [
                "Psychischer Status vollständig erhoben",
                "Risiko (SUI/Fremdgefährdung) aktiv exploriert",
                "Ressourcen/Schutzfaktoren dokumentiert",
            ],
        }
    else:
        if isinstance(result, dict) and "status_checkliste" not in result:
            result["status_checkliste"] = [
                "Psychischer Status vollständig erhoben",
                "Risiko (SUI/Fremdgefährdung) aktiv exploriert",
                "Ressourcen/Schutzfaktoren dokumentiert",
            ]

    return result, bef_text


# ------------------ Einschätzung + Prozedere ------------------

def generate_assessment_and_plan_german(
    anamnese_final: str,
    status_final: str,
    humanize: bool = True,
    phase: str = "initial",
) -> Tuple[str, str]:
    """Erzeugt 'Einschätzung' (Hypothesen + Schweregrad/Dringlichkeit) und 'Prozedere'."""
    try:
        path = resolve_red_flags_path(prefer_psych=True)
        red_flags_data = load_red_flags(path)
        rf_hits = check_red_flags(anamnese_final + "\n" + status_final, red_flags_data, return_keywords=True) or []
        red_flags_list = [f"{kw} – {msg}" for (kw, msg) in rf_hits]
    except Exception:
        red_flags_list = []

    note = _swiss_style_note(humanize)

    sys_part = (
        "Du bist erfahrener Psychologe in einer Schweizer Praxis (ambulante Konsultation).\n"
        + note
        + "\nNur notwendige Infos; keine Wiederholungen von bereits Gesagtem. "
        + "Schweizer/Europäische Good Practice priorisieren. Kein ß, nur ss."
    ).strip()

    usr = {
        "anamnese": anamnese_final,
        "status": status_final,
        "phase": phase,
        "red_flags": red_flags_list,
    }

    prompt = (
        sys_part
        + "\n\nErzeuge zwei fertige Felder:\n\n"
        + "Einschätzung:\n"
        + "- Psychologische Haupt-Hypothese(n) mit ausführlicher Begründung, inkl. Schweregrad/Dringlichkeit.\n"
        + "- 2–4 Alternativhypothesen/Komorbiditäts-Erwägungen (nur wenn sinnvoll), ohne Status zu wiederholen.\n"
        + "- Falls Red Flags vorliegen (z.B. Suizidalität/Fremdgefährdung/akute Psychose): kurz einordnen (Dringlichkeit/Sicherheitsbedarf).\n\n"
        + "Prozedere:\n"
        + "- Klare Bulletpoints: Setting & Interventionen (Psychoedukation, Aktivitätsaufbau, Exposition/Skills, KVT-Elemente), Hausaufgaben/Arbeitsauftrag.\n"
        + "- Sicherheit: Krisen-/Sicherheitsplan, Notfallkontakte; Einbezug Dritter (Angehörige/Hausarzt/Psychiatrie) nach Einwilligung.\n"
        + "- Verlauf/Kontrolle: Indikation zur stationären Behandlung oder konkretes Intervall.\n"
        + "- Medikamente nur als Koordination mit ärztlichen Stellen (keine Dosierungen).\n"
        + "- Optional: Diagnostische Vertiefung (Screenings) nur wenn angezeigt.\n\n"
        + "Antwort: gib zuerst 'Einschätzung', dann eine Leerzeile, dann 'Prozedere'.\n"
    )

    text = ask_openai(prompt + "\n\n" + json.dumps(usr, ensure_ascii=False))

    parts = [p.strip() for p in text.strip().split("\n\n", 1)] if text else []
    beurteilung = parts[0] if parts else ""
    prozedere = parts[1] if len(parts) > 1 else ""

    return beurteilung, prozedere


__all__ = [
    "resolve_red_flags_path",
    "generate_full_entries_german",
    "generate_anamnese_gaptext_german",
    "generate_status_gaptext_german",
    "generate_assessment_and_plan_german",
    "format_anamnese_fliess_text",   
]
