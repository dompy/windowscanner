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
from typing import Any, Dict, List, Optional, Tuple, Optional, List, Tuple
from openai import OpenAI
from red_flags_checker import check_red_flags, load_red_flags
import logging

# ------------------ Config & Client ------------------
logging.basicConfig(level=logging.INFO)
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
    """Deutsch, kurz & präzise. Fällt weich zurück, wenn kein Client."""
    if client is None:
        return "KI nicht verfügbar (API-Key fehlt)."
    try:
        resp = client.chat.completions.create(
            model=MODEL_DEFAULT,
            messages=[
                {"role": "system", "content": "Antworte ausschließlich auf Deutsch. Knapp, präzise, praxisnah."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logging.exception("OpenAI-Fehler: %s", e)
        return "KI-Antwort fehlgeschlagen (siehe Log)."


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


def _swiss_style_note(humanize: bool) -> str:
    if humanize:
        return (
            "Schreibe kurz, telegraphisch. Schweizer Orthografie (ss). "
            "Du darfst 1–2 minimale Tippfehler einbauen (z. B. Buchstabenvertauschung), "
            "aber keine Fachbegriffe oder Zahlen verfälschen."
        )
    return "Schreibe kurz, telegraphisch. Schweizer Orthografie (ss). Keine Tippfehler."


def _compose_context_for_exam(
    anamnese_raw: str,
    zusatzfragen: Optional[List[str]] = None,
    zusatzfragen_qa: Optional[List[Tuple[str, str]]] = None,
) -> str:
    """
    Baut einen schlanken Kontextblock: freie Anamnese (+ optionale Zusatzfragen / Q&A).
    """
    parts: List[str] = []
    ana = (anamnese_raw or "").strip()
    parts.append(f"Anamnese (wortgetreu):\n{ana if ana else 'keine Angaben'}")
    if zusatzfragen:
        z = [s for s in zusatzfragen if s]
        if z:
            parts.append("Zusatzfragen (Vorschläge):\n- " + "\n- ".join(z[:5]))
    if zusatzfragen_qa:
        qa_lines = [f"- {q.rstrip('?')}? → {a}" for (q, a) in zusatzfragen_qa if q and a]
        if qa_lines:
            parts.append("Zusatzfragen – beantwortet:\n" + "\n".join(qa_lines))
    return "\n\n".join(parts)

def _compose_context_for_assessment(
    anamnese_raw: str,
    befunde_text: str = "",
    zusatzfragen: Optional[List[str]] = None,
    zusatzfragen_qa: Optional[List[Tuple[str, str]]] = None,
) -> str:
    """Kombiniert Anamnese, Zusatzfragen (optional mit Antworten) und erhobene Befunde zu einem klaren Kontextblock."""
    parts: List[str] = []
    ana = (anamnese_raw or "").strip() or "keine Angaben"
    parts.append(f"Anamnese (frei, wortgetreu):\n{ana}")

    if zusatzfragen:
        z = [s for s in zusatzfragen if s]
        if z:
            parts.append("Zusatzfragen (Systemvorschläge):\n- " + "\n- ".join(z[:5]))

    if zusatzfragen_qa:
        qa_lines = [f"- {q.rstrip('?')}? → {a}" for (q, a) in zusatzfragen_qa if q and a]
        if qa_lines:
            parts.append("Zusatzfragen – beantwortet (Nutzer):\n" + "\n".join(qa_lines))

    bef = (befunde_text or "").strip()
    if bef:
        parts.append("Erhobene Befunde (aktueller Stand):\n" + bef)

    return "\n\n".join(parts)

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

    result = {k: (result.get(k) or "keine Angaben").strip()
            for k in ("anamnese_text","befunde_text","beurteilung_text","prozedere_text")}
    if red_flags_list:
        result_with_rf = dict(result)
        result_with_rf["red_flags"] = red_flags_list
    else:
        result_with_rf = result
    full_block = _format_full_entries_block(result_with_rf)
    return result_with_rf, full_block


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

def generate_zusatzfragen_json(
    anamnese_raw: str, answered_context: Optional[str] = ""
) -> Dict[str, Any]:
    """Erzeugt 2–5 Zusatzfragen (nur JSON, kein Fliesstext)."""
    sys_msg = (
        "Du bist ein erfahrener Hausarzt in der Schweiz.\n"
        "Aufgabe: 2–5 gezielte, patientenverständliche Zusatzfragen. "
        "Nur Fragen, keine Untersuchungen. Antwort NUR als JSON {\"zusatzfragen\": [...] }."
    )
    usr = {
        "eingabe_freitext": anamnese_raw,
        "bereits_beantwortet": answered_context or "",
    }
    result = _ask_openai_json(
        messages=[{"role": "system", "content": sys_msg},
                  {"role": "user", "content": json.dumps(usr, ensure_ascii=False)}]
    )
    # Schema-Schutz
    if not isinstance(result, dict) or "zusatzfragen" not in result:
        result = {"zusatzfragen": []}
    # clamp 2–5
    zs = result.get("zusatzfragen") or []
    result["zusatzfragen"] = zs[:5] if len(zs) > 5 else zs
    return result


def generate_befunde_gaptext_german(
    anamnese_raw: str,
    phase: str = "initial",
    zusatzfragen: Optional[List[str]] = None,
    zusatzfragen_qa: Optional[List[Tuple[str, str]]] = None,
) -> tuple[dict, str]:
    """
    Erzeugt Lückentext + Checkliste. Verwendet Anamnese + (option.) Zusatzfragen/Q&A als Kontext.
    Rückgabe: (payload_json, befunde_text_block)
    """
    context = _compose_context_for_exam(anamnese_raw, zusatzfragen, zusatzfragen_qa)
    sys_msg = (
        "Du bist ein erfahrener Hausarzt (Schweiz). "
        "Erzeuge praxisnahe, rasch verfügbare Untersuchungen/Befunde als Lückentext + Checkliste. "
        "Keine Vitalparameter. Keine Dopplungen zur Anamnese. "
        f"Phase='{phase}'. Wenn phase='persistent': am Ende 2–3 sinnvolle Erweiterungen beginnen mit 'Bei Persistenz/Progredienz: …'. "
        "Antworte NUR als JSON mit Feldern 'befunde_lueckentext' und 'befunde_checkliste'."
    )
    usr = {"kontext": context, "phase": phase}
    result = _ask_openai_json([
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": json.dumps(usr, ensure_ascii=False)}
    ])
    # Schema-Absicherung
    lz = (result or {}).get("befunde_lueckentext") or "- keine Angaben"
    chk = (result or {}).get("befunde_checkliste") or []
    payload = {"befunde_lueckentext": lz, "befunde_checkliste": chk}
    # Schön formatierter Block für das Textfeld
    block = lz.strip() + ("\n\n" + "\n".join(f"- [ ] {x}" for x in chk) if chk else "")
    return payload, block


def on_gaptext(self):
    raw = self.fields["Anamnese"].get("1.0", tk.END).strip()
    if not raw:
        messagebox.showwarning("Hinweis", "Bitte zuerst Anamnese (frei) eingeben.")
        return
    try:
        payload = generate_zusatzfragen_json(raw)
    except Exception as e:
        messagebox.showerror("Fehler", f"Zusatzfragen fehlgeschlagen:\n{e}")
        return
    self.txt_gap.delete("1.0", tk.END)
    for q in payload.get("zusatzfragen", []):
        self.txt_gap.insert(tk.END, f"- {q}\n")



# ------------------ Schritt 2: Befunde (Basis / optional erweitert) ------------------

def suggest_basic_exams_german(
    anamnese_raw: str,
    phase: str = "initial",
    zusatzfragen: Optional[List[str]] = None,
    humanize: bool = False,
    zusatzfragen_qa: Optional[List[Tuple[str, str]]] = None,
) -> str:
    """
    Liefert ein fertiges Feld "Befunde" (kurze Sätze/Telegraphie).
    phase: 'initial' = schlank/basisnah; 'persistent' = am Ende Erweiterungen.
    Nutzt Anamnese + (optional) Zusatzfragen/Q&A als Kontext.
    """
    # Phase absichern
    phase_norm = "persistent" if str(phase).lower().strip() == "persistent" else "initial"

    # Kontext bauen
    ctx = _compose_context_for_exam(anamnese_raw, zusatzfragen, zusatzfragen_qa)

    # Stil-Hinweis (falls deine Helferfunktion existiert)
    try:
        note = _swiss_style_note(humanize)  # type: ignore[name-defined]
    except Exception:
        # Fallback: sachlich, kurz, CH-Orthografie
        note = (
            "Schreibe sachlich, telegraphisch, ohne Floskeln. "
            "Schweizer Orthografie (ss, keine ß). Keine erfundenen Vitalparameter/Dosen."
        )

    sys_msg = (
        "Du bist erfahrener Hausarzt in einer Schweizer Hausarztpraxis.\n"
        f"{note}\n"
        "Aufgabe: Erzeuge praxisnahe, rasch verfügbare Untersuchungen/Befunde als Freitext "
        "(kein JSON). Nur Punkte, die in der Grundversorgung sofort machbar sind. "
        "Kein Overkill; dedupliziere gegen bereits erhobene Angaben."
    ).strip()

    vorgaben = (
        "Vorgaben:\n"
        "- Zuerst Kurzstatus: AZ (kurz, objektiv).\n"
        "- Danach fokussierte körperliche Untersuchung gemäss Leitsymptom.\n"
        "- Optional Basisgeräte/POCT, wenn sinnvoll: EKG, Lungenfunktion, Labor (3–6 relevante Parameter), Schellong.\n"
        "- Bei phase=\"persistent\": am Ende genau eine Zeile beginnen mit "
        "\"Bei Persistenz/Progredienz:\" und 2–3 sinnvolle weiterführende Untersuchungen.\n"
        "- Keine Vitalparameter notieren, keine Medikamente oder Diagnosen, nur Untersuchungen/Befunde.\n"
        "- Keine Dopplungen zu bereits erwähnten/erhobenen Punkten.\n"
        "- Schweizer Standards."
    )

    # Nutzlast (klar getrennt, damit das Modell strukturiert arbeiten kann)
    usr = {
        "phase": phase_norm,
        "kontext": ctx,
    }

    prompt = (
        f"{sys_msg}\n\n{vorgaben}\n\n"
        "Antwort: Gib NUR das Feld «Befunde» als zusammenhängenden, praxisnahen Text "
        "(Freitext, keine Listen mit [ ] und kein JSON).\n\n"
        f"{json.dumps(usr, ensure_ascii=False)}"
    )

    # LLM-Aufruf
    out = ask_openai(prompt)  # erwartet String-Return deiner bestehenden Infrastruktur
    return (out or "").strip()


# ------------------ Schritt 3: Beurteilung + Prozedere ------------------

def generate_assessment_and_plan_german(
    anamnese_raw: str,
    befunde: str = "",
    zusatzfragen: Optional[List[str]] = None,
    zusatzfragen_qa: Optional[List[Tuple[str, str]]] = None,
    humanize: bool = False,
) -> tuple[str, str]:
    """
    Erzeugt:
      - beurteilung_text: Verdachtsdiagnose + 2–4 DDs, jeweils kurz begründet.
      - prozedere_text: Bulletpoints (nächste Schritte in der Grundversorgung, Verlauf/Kontrolle,
                        vorzeitige Wiedervorstellung (allgemein, ohne Red-Flag-Listen), Medikation nur allgemein).
        Abschlusszeile: "Bei Persistenz/Progredienz: …".

    Nutzt kombinierte Informationen aus Anamnese, (beantworteten) Zusatzfragen und erhobenen Befunden.
    """
    # Stilhinweis (falls vorhanden)
    try:
        note = _swiss_style_note(humanize)  # noqa: F821  (falls Helper in deinem Projekt existiert)
    except Exception:
        note = ("Schreibe kurz, telegraphisch, sachlich. Schweizer Orthografie (ss). "
                "Keine Dosierungen oder Vitalparameter erfinden. Keine Red-Flags im Text.")

    ctx = _compose_context_for_assessment(anamnese_raw, befunde, zusatzfragen, zusatzfragen_qa)

    sys_msg = (
        "Du bist erfahrener Hausarzt in der Schweiz. "
        "Ziel: kurze, praxisnahe Beurteilung und ein konkretes Prozedere für die Grundversorgung. "
        f"{note}"
    )

    richtlinien = (
        "Richtlinien:\n"
        "- Beurteilung: Verdachtsdiagnose zuerst; 2–4 relevante Differenzialdiagnosen, je 1 kurze Begründung "
        "(ohne Wiederholung langer Befunde; keine Vitalparameter; keine Dosierungen).\n"
        "- Prozedere: Bulletpoints; nächste Schritte (ausschliesslich rasch verfügbare Untersuchungen/POCT), "
        "Verlauf/Kontrolle, vorzeitige Wiedervorstellung bei Warnzeichen (allgemein formuliert, "
        "keine konkrete Red-Flag-Liste), Medikation nur allgemein. "
        "Am Ende genau eine Zeile mit \"Bei Persistenz/Progredienz:\" und 1–2 sinnvollen Abklärungen/Überweisungen.\n"
        "- Schweizer Standards. Keine Red Flags im Text aufzählen."
    )

    user_payload = {
        "kontext": ctx,
        "schema": {"beurteilung_text": "string", "prozedere_text": "string"},
        "sprache": "Deutsch (Schweiz)",
        "stil": "kurz, telegraphisch",
    }

    prompt_user = (
        f"{richtlinien}\n\n"
        "Antwort: Gib NUR gültiges JSON mit GENAU diesen Feldern (keine weiteren):\n"
        "{\n"
        '  "beurteilung_text": "<kurzer Text>",\n'
        '  "prozedere_text": "<Bulletpoints/kurze Sätze>"\n'
        "}\n\n"
        f"{json.dumps(user_payload, ensure_ascii=False)}"
    )

    # Primär: strukturierte JSON-Antwort
    try:
        result = _ask_openai_json([   # noqa: F821  (dein vorhandener JSON-Helper)
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": prompt_user},
        ])
    except Exception:
        result = None

    beurteilung = (result or {}).get("beurteilung_text") if isinstance(result, dict) else None
    prozedere  = (result or {}).get("prozedere_text") if isinstance(result, dict) else None

    # Fallback: unstrukturierter Aufruf (falls JSON-Helper nicht verfügbar)
    if not (beurteilung and prozedere):
        try:
            raw = ask_openai(sys_msg + "\n\n" + prompt_user)  # noqa: F821  (dein Fallback-Helper)
            # sehr schlanke Rettung, falls Rohtext zurückkommt
            beurteilung = beurteilung or (raw.split("Prozedere:")[0].strip() if "Prozedere:" in raw else raw.strip())
            prozedere  = prozedere  or (raw.split("Prozedere:", 1)[1].strip() if "Prozedere:" in raw else "• keine Angaben")
        except Exception:
            beurteilung = beurteilung or "keine Angaben"
            prozedere  = prozedere  or "• keine Angaben"

    return (beurteilung or "keine Angaben").strip(), (prozedere or "• keine Angaben").strip()

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
    return procedure
