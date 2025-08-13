# gpt_logic.py — CLEAN & SWISS-STYLE

import os, re, json
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

# ------------------ Globaler Stil / Leitplanken ------------------

PREFIX_PROMPT = """
This GPT is a professional-grade medical assistant for physicians in Switzerland
(then Europe, UK, USA in that order). It provides concise, guideline-aligned,
evidence-based help for diagnostic reasoning and management in primary care.

Prioritise Swiss sources (BAG/FOPH, Swiss medical societies). If unavailable,
use European (ESC/EMA/WHO), then UK (NICE), then US (CDC/NIH/FDA).
Do not replace clinical judgement; avoid speculation; state uncertainty.
Use precise professional terminology; CH-Deutsch (ss statt ß).

When explicitly asked for sources, cite **named sources** (guideline/journal + year).
Links are optional in this app (no browsing). If Swiss-specific data is unknown,
say so and qualify as consensus/clinical convention.

STIL:
- 2–6 telegraphische Sätze, CH-Deutsch.
- Keine Bullet-Listen im Fliesstext (ausser bei „Checkliste“-Schritt).
- Keine Nachsatz-Platzhalter.

PLATZHALTER-POLICY:
- 3–6 **Inline**-Platzhalter (z. B. „seit [__]“, „NRS [__/10]“, „Seite [__]“).
- Keine vagen Platzhalter („Bereich [__]“, „Schnittstelle [__]“).
- Bereits beantwortetes nicht erneut als Lücke markieren.

SALIENZ:
- Frage/nutze nur, was Diagnose/Management/Dringlichkeit ändert.
""".strip()


ANAMNESE_DIM_PROMPT = """
KLINISCHE DIMENSIONEN (Leitplanken; NICHT als Liste ausgeben):
1) Lokalisation – Wo? (Stelle/Seite [__])
2) Beginn/Verlauf – Seit wann? (seit [__]; plötzlich/schleichend [__]; Verlauf [__])
3) Charakter – Wie? (stechend/drückend/brennend/kolikartig [__])
4) Intensität – Wie stark? (NRS [__/10]; Alltagsbeeinträchtigung [__])
5) Ausstrahlung – Wohin? (strahlt nach [__])
6) Modulierende Faktoren – Was ändert es? (bessert/verschlechtert [__])
7) Begleitsymptome – Was gleichzeitig? (Fieber/Übelkeit/Dyspnoe/neurologisch [__])
""".strip()

ASSERTIONS_GUARD = """
ASSERTIONS-GUARD:
- Behaupte nur konkrete Fakten, die unter 'explizite_fakten' stehen.
- Falls nicht vorhanden: neutral formulieren und **[__]** setzen, nicht raten/verneinen.
- Negative Aussagen NUR, wenn sie unter 'negationen_explizit' stehen.
- Schmerzcharakter NIE raten – falls unbekannt -> „Charakter [__]“.
""".strip()

ASSERTIONS_GUARD_BEFUNDE = """
ASSERTIONS-GUARD (Befunde):
- Keine konkreten Messwerte/Normalbefunde eintragen, ausser sie stehen explizit in 'explizite_fakten'.
- Zahlen IMMER als [__]; Optionen als (gut/mittel/reduziert) OHNE Vorauswahl.
- Keine unbegründeten Negationen; fehlende Infos -> [__] oder Option.
- Keine Wiederholung von Anamnese-Inhalten (subjektive Symptome).
""".strip()

ASSERTIONS_GUARD_AP = """
ASSERTIONS-GUARD (Beurteilung/Prozedere):
- Jede Aussage stützt sich auf 'explizite_fakten'/'negationen_explizit' aus Anamnese/Befunden.
- KEINE neuen Messwerte/Negativbefunde ohne Quelle.
- Fehlende Infos -> konditional (z. B. „bei ausbleibender Besserung …“).
- Medikamente ohne erfundene Dosierungen.
""".strip()

def _infer_context(anamnese_text: str) -> str:
    t = (anamnese_text or "").lower()
    if any(w in t for w in ["husten","schnupf","halsschmerz","fieber","erkält","grippe","auswurf","dyspnoe","soor","otitis"]):
        return "resp"
    if any(w in t for w in ["bauch","übel","erbrech","durchfall","stuhl","abdomen","kolik"]):
        return "gi"
    if any(w in t for w in ["rücken","hand","knie","verstaucht","sturz","trauma","schulter","faustschluss","schwellung"]):
        return "msk"
    return "generic"

def _adaptive_min_dims(anamnese_text: str) -> int:
    ctx = _infer_context(anamnese_text)
    return 3 if ctx in ("resp","gi") else 5


# ------------------ Low-level Helpers ------------------

def _ask_openai_json(messages: List[Dict[str, str]], model: str = MODEL_DEFAULT, temperature: float = 0.2) -> Dict[str, Any]:
    resp = client.chat.completions.create(
        model=model, messages=messages, temperature=temperature,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or "{}"
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {"raw_text": content}

def ask_openai_text(messages: List[Dict[str, str]], model: str = MODEL_DEFAULT, temperature: float = 0.2) -> str:
    resp = client.chat.completions.create(model=model, messages=messages, temperature=temperature)
    return (resp.choices[0].message.content or "").strip()

def _swiss_style_note(humanize: bool = True) -> str:
    base = "Schweizer Orthografie (ss statt ß). Natürlich wie hausärztliche KG-Einträge; kurz/telegraphisch; "
    if humanize:
        base += "minimale Tippfehler/Verkürzungen toleriert; "
    base += "keine Floskeln, keine Romane."
    return base

def build_messages(prefix_blocks: List[str], user_payload: dict, strict_note: str = "") -> List[Dict[str, str]]:
    sys = "\n\n".join([b.strip() for b in prefix_blocks if b and b.strip()])
    if strict_note:
        sys += "\n\nZUSATZ – STRIKT:\n" + strict_note.strip()
    # <— wichtig für response_format={"type": "json_object"}
    sys += "\n\nFORMAT-HINWEIS: Antworte NUR als json-Objekt (keine Prosa)."
    return [
        {"role": "system", "content": sys},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


# -------- Guards / Extractors --------

def _extract_explicit_facts(text: str) -> dict:
    sys = ("Extrahiere NUR explizit genannte Fakten und Negationen als json. "
           "Keine Spekulation/Ableitungen.\n"
           "Schema: {\"facts_explicit\": [\"...\"], \"negations_explicit\": [\"...\"]}")
    out = _ask_openai_json([
        {"role": "system", "content": sys},
        {"role": "user", "content": json.dumps({"text": text}, ensure_ascii=False)}
    ])
    if isinstance(out, dict):
        return {
            "facts_explicit": out.get("facts_explicit") or [],
            "negations_explicit": out.get("negations_explicit") or [],
        }
    return {"facts_explicit": [], "negations_explicit": []}

_CHAR_ROOTS = ["stech", "drück", "drueck", "brenn", "dumpf", "kolik", "zieh", "bohr", "poch"]
def _assertion_violations_char_neg(input_text: str, output_text: str) -> bool:
    inp = input_text.lower(); out = output_text.lower()
    char_inp = any(r in inp for r in _CHAR_ROOTS)
    char_out = any(r in out for r in _CHAR_ROOTS)
    if char_out and not char_inp:
        return True
    if re.search(r"\b(keine|ohne|nicht)\b", out) and not re.search(r"\b(keine|ohne|nicht)\b", inp):
        return True
    return False

# Messwerte ausserhalb von [__] erkennen
_MEAS_UNITS = r"(mm\s*hg|mmhg|/min|bpm|%|°\s*c|°c|degc)"
def _has_unbracketed_measurements(txt: str) -> bool:
    return bool(re.search(r"\b\d[\d\.,]*\s*(?:" + _MEAS_UNITS + r")\b", (txt or "").lower()))

def _assertion_violations_generic(source_text: str, output_text: str) -> bool:
    if re.search(r"\b(keine|ohne|nicht)\b", (output_text or "").lower()) and not re.search(r"\b(keine|ohne|nicht)\b", (source_text or "").lower()):
        return True
    if _has_unbracketed_measurements(output_text or ""):
        return True
    return False

# -------- Coverage Heuristics (7 Dimensionen) --------

_DIM_PATTERNS = {
    "lokalisation": r"(lokalis|stelle|region|seite|rechts|links|mittig|retrostern|epigastr|abdomen|bauch|thorax|brust|rücken|leiste|flanke|kopf|hand|arm|bein)",
    "beginn_verlauf": r"(seit|beginn|plötz|ploetz|schleich|verlauf|progred|intermittierend|kontinuierlich|akut|chron)",
    "charakter": r"(charakter|stech|drück|drueck|brenn|dumpf|kolik|krampf|zieh)",
    "intensität": r"(nrs|intensit|stärk|staerk|0[-–/]?10|/10|10/10)",
    "ausstrahlung": r"(ausstrahl|strahlt|radi)",
    "modulierend": r"(besser|schlecht|lage|beweg|nahr|essen|medika|ruhe|belast)",
    "begleit": r"(begleit|fieber|übel|uebel|erbrech|dyspnoe|atemnot|schwind|neurolog|lähm|laehm|taub|gewichtsverlust|nachtschweiss)"
}
def _dimension_coverage(txt: str) -> set:
    t = (txt or "").lower(); hits = set()
    for k, pat in _DIM_PATTERNS.items():
        if re.search(pat, t):
            hits.add(k)
    return hits

def _bad_anamnese_output(input_text: str, gap_text: str, min_dims: int = 5) -> bool:
    t = (gap_text or "").strip()
    if not t: return True
    if t.startswith("- ") or "\n- " in t or "fragen:" in t.lower(): return True
    if re.search(r"\[[ _]*__?[ _]*\]\s*$", t): return True  # End-Lücke
    nph = len(re.findall(r"\[[^\]]*__?[^\]]*\]", t))
    if nph < 3 or nph > 6: return True
    if re.search(r"(schnittstelle|stelle|bereich)\s*\[[^\]]*\]", t.lower()): return True
    if re.sub(r"\s+", " ", input_text).strip().lower() == re.sub(r"\s+", " ", t).strip().lower(): return True
    if len(_dimension_coverage(t)) < min_dims: return True
    return False

# ------------------ „Alles generieren“ (4 Felder) ------------------

def _format_full_entries_block(payload: Dict[str, Any]) -> str:
    parts = []
    parts += ["Anamnese:", (payload.get("anamnese_text") or "keine Angaben").strip(), ""]
    parts += ["Befunde:", (payload.get("befunde_text") or "keine Angaben").strip(), ""]
    parts += ["Beurteilung:", (payload.get("beurteilung_text") or "keine Angaben").strip(), ""]
    parts += ["Prozedere:", (payload.get("prozedere_text") or "keine Angaben").strip()]
    return "\n".join(parts).strip()

def generate_full_entries_german(user_input: str, context: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, str], str]:
    context = context or {}
    try:
        red_flags_data = load_red_flags(RED_FLAGS_PATH)
        rf_hits = check_red_flags(user_input, red_flags_data, return_keywords=True) or []
        red_flags_list = [f"{kw} – {msg}" for (kw, msg) in rf_hits]
    except Exception:
        red_flags_list = []

    sys_msg = (
        "Du bist ein medizinischer Assistent in einer Schweizer Hausarztpraxis.\n"
        "Ziel: Erzeuge vier dokumentationsfertige Felder (Deutsch), direkt kopierbar.\n"
        "- Nichts erfinden. Wo Angaben fehlen: 'keine Angaben', 'nicht erhoben' oder 'noch ausstehend'.\n"
        "- Stil: Anamnese kurz/telegraphisch (Dauer, Lokalisation/Qualität, Begleitsymptome, Relevantes); "
        "Befunde objektiv (AZ, Orientierung, Status, Vitalparameter); "
        "Beurteilung (Arbeitsdiagnose + 2–4 DD); "
        "Prozedere (Massnahmen, Verlauf, Warnzeichen; Medikation ohne erfundene Dosierungen).\n"
        "Antwort nur als json mit Schlüsseln anamnese_text, befunde_text, beurteilung_text, prozedere_text."
    ).strip()
    usr_payload = {"eingabetext": user_input, "kontext": context}
    result = _ask_openai_json([{"role": "system", "content": sys_msg}, {"role": "user", "content": json.dumps(usr_payload, ensure_ascii=False)}])
    if red_flags_list:
        result["red_flags"] = red_flags_list
    return result, _format_full_entries_block(result)

# ------------------ Schritt 1: Anamnese → Lückentext ------------------

ANAMNESE_STEP_PROMPT = """
AUFGABE:
- Formuliere den Freitext zu einer **integrierten, fokussierten Anamnese** um.
- Nutze die 7 Dimensionen nur als Leitplanken und **lasse irrelevante Achsen weg**.
  Beispiele:
  • Infekt/URI: Fokus auf Beginn/Verlauf, Fieberspitze, Husten/Auswurf (Farbe), Dyspnoe,
    Umgebungsanamnese/Kontakte; i. d. R. **keine** Seite/Lokalisation.
  • Muskuloskelettal/Trauma: Seite/Dominanz, Mechanismus, Belastbarkeit, Neurovaskulär.
- Setze **3–6** präzise Inline-Platzhalter im Satz.

QUALITÄT:
- Keine Spiegelung des Eingabetextes. Nur salienz-relevante Details.
""".strip()

CHECKLIST_PREFIX = """
Ziel: Ergänze den Freitext um die **wichtigsten Nachfragen** als **Bullet-Checkliste** (8–12 Punkte),
damit ein Assistenzarzt schnell die relevanten Angaben erheben kann.
Nur Fragen/Prompts, keine fertigen Aussagen. Optionen oder [__] angeben.
Passe die Inhalte dem Kontext an (resp/gi/msk/...).
""".strip()

def generate_anamnese_checklist_german(anamnese_raw: str, humanize: bool = True, max_items: int = 12) -> Tuple[dict, str]:
    facts = _extract_explicit_facts(anamnese_raw)
    ctx = _infer_context(anamnese_raw)
    hints = {
        "resp": [
            "- Beginn seit [__] (ploetzlich/schleichend)",
            "- Fieberspitze [__] °C",
            "- Husten (ja/nein: [__]); Auswurf (ja/nein: [__]); Farbe (klar/gelb/grün [__])",
            "- Dyspnoe (Ruhe/Belastung [__])",
            "- Halsschmerzen (ja/nein [__]); Dysphagie (ja/nein [__])",
            "- Umgebungsanamnese: Kontakt zu Erkrankten (ja/nein [__])",
            "- Reise/Heime/Arbeitsumfeld (ja/nein [__])",
            "- Risikofaktoren (immunsupprimiert/SSW/chron. Lungen-/Herzerkrankung [__])"
        ],
        "gi": [
            "- Beginn seit [__]; Verlauf [__]",
            "- Schmerzlokalisation [__]; Charakter [__]; Ausstrahlung [__]",
            "- Übelkeit/Erbrechen (ja/nein [__])",
            "- Stuhl: Diarrhoe/Obstipation/Blut [__]",
            "- Fieberspitze [__] °C",
            "- NSAR/Alkohol [__]; OP-Anamnese [__]"
        ],
        "msk": [
            "- Seite (rechts/links [__]); Dominante Seite [__]",
            "- Mechanismus/Trauma (ja/nein; [__])",
            "- Belastbarkeit/Beweglichkeit [__]",
            "- Neurovaskulär: Sens/Motorik/Durchblutung (ja/nein [__])",
            "- Schwellung/Deformität (ja/nein [__])",
            "- Lindernd/Trigger [__]"
        ],
        "generic": [
            "- Beginn seit [__]; Verlauf [__]",
            "- Charakter [__]; Intensität NRS [__/10]",
            "- Ausstrahlung (ja/nein [__])",
            "- Modulierende Faktoren (bessert [__]/verschlechtert [__])",
            "- Begleitsymptome [__]"
        ]
    }[ctx]
    base = [
        "- Funktion/Alltag/Arbeitsfaehigkeit [__]",
        "- Relevante Vorerkrankungen/Medikation (inkl. Antikoagulation) [__]"
    ]
    lines = (hints + base)[:max_items]
    text = "\n".join(lines)
    payload = {"checklist_text": text, "items": lines, "context": ctx}
    return payload, text

FINALIZE_PREFIX = """
Erzeuge aus Freitext + **ausgefüllter** Checkliste eine **integrierte, professionelle Anamnese**.
3–6 kurze Sätze; CH-Deutsch; keine Bullets, keine Platzhalter. Nichts erfinden.
""".strip()

def compose_anamnese_from_freetext_and_answers(anamnese_raw: str, checklist_filled_text: str, humanize: bool = True) -> Tuple[dict, str]:
    facts = _extract_explicit_facts(anamnese_raw + "\n" + checklist_filled_text)
    usr = {
        "eingabe_freitext": anamnese_raw,
        "checkliste_ausgefuellt": checklist_filled_text,
        "explizite_fakten": facts.get("facts_explicit"),
        "negationen_explizit": facts.get("negations_explicit")
    }
    messages = build_messages([PREFIX_PROMPT, FINALIZE_PREFIX], usr)
    result = _ask_openai_json(messages)
    final_text = (result or {}).get("anamnese_final", "").strip() if isinstance(result, dict) else ""
    if not final_text:
        # Minimal-Fallback
        lines = [l.strip("- ").strip() for l in checklist_filled_text.splitlines() if l.strip().startswith("-")]
        core = "; ".join(lines[:6])[:500]
        final_text = (anamnese_raw.strip() + " " + core + ".").strip()
        result = {"anamnese_final": final_text}
    return result, final_text


def generate_anamnese_gaptext_german(anamnese_raw: str, answered_context: Optional[str] = "", humanize: bool = True) -> Tuple[Dict[str, Any], str]:
    facts = _extract_explicit_facts(anamnese_raw)
    usr = {
        "eingabe_freitext": anamnese_raw,
        "bereits_beantwortet": answered_context or "",
        "explizite_fakten": facts.get("facts_explicit"),
        "negationen_explizit": facts.get("negations_explicit"),
        "hinweise": "3–6 Inline-Lücken im Satz; nichts erfinden, nichts grundlos verneinen."
    }
    messages = build_messages(
        [PREFIX_PROMPT, ANAMNESE_DIM_PROMPT, ANAMNESE_STEP_PROMPT, ASSERTIONS_GUARD],
        usr
    )
    result = _ask_openai_json(messages)
    gap_text = (result or {}).get("anamnese_lueckentext", "").strip() if isinstance(result, dict) else ""

    def _bad(txt: str) -> bool:
        return _bad_anamnese_output(anamnese_raw, txt, min_dims=_adaptive_min_dims(anamnese_raw)) \
            or _assertion_violations_char_neg(anamnese_raw, txt)


    if isinstance(result, dict) and not _bad(gap_text):
        review = _ask_openai_json(build_messages([PREFIX_PROMPT, "AUTO-REVIEW:\n- Streiche Irrelevantes; behalte 3–6 Lücken; evtl. 1–2 fallspezifische Präzisierungen."], {"original_freitext": anamnese_raw, "entwurf": result}))
        cand = (review or {}).get("anamnese_lueckentext", "").strip() if isinstance(review, dict) else ""
        if cand and not _bad(cand):
            result, gap_text = review, cand

    if _bad(gap_text):
        strict = "Mindestens 5/7 Dimensionen abdecken, 3–6 Inline-Lücken. Keine neuen Eigenschaften/Negationen erfinden; Unklares -> [__]."
        result2 = _ask_openai_json(build_messages([PREFIX_PROMPT, ANAMNESE_DIM_PROMPT, ANAMNESE_STEP_PROMPT, ASSERTIONS_GUARD], usr, strict))
        gt2 = (result2 or {}).get("anamnese_lueckentext", "").strip() if isinstance(result2, dict) else ""
        if gt2 and not _bad(gt2):
            result, gap_text = result2, gt2

    if _bad(gap_text):
        base = re.sub(r"\s+", " ", anamnese_raw.strip())
        parts = [
            f"{base} (seit [__], Verlauf [__]).",
            "Lokalisation/Seite [__]; Ausstrahlung [__].",
            "Charakter [__]; Intensität (NRS [__/10]).",
            "Modulierende Faktoren (bessert [__]/verschlechtert [__]).",
            "Begleitsymptome [__].",
        ]
        gap_text = " ".join(parts)
        result = {"anamnese_lueckentext": gap_text, "offene_punkte_checkliste": ["Lokalisation/Seite", "Beginn/Verlauf", "Charakter", "Intensität (NRS)", "Ausstrahlung", "Modulierende Faktoren", "Begleitsymptome"]}

    return result, gap_text

# ------------------ Schritt 2: Befunde (Basis / optional erweitert) ------------------

BEFUNDE_STEP_PROMPT = """
AUFGABE (Befunde-Lückentext):
- Erzeuge eine **ausfüllbare** Befunde-Vorlage als Fliesstext/Zeilen.
- 6–10 kurze, praxisnahe Zeilen (AZ/Orientierung, Vitalparameter, gezielter Status, Basis-POCT/Labor, ggf. EKG).
- Zahlen IMMER als [__]; Optionen in Klammern (z. B. (gut/mittel/reduziert)); keine vorgewählten Werte.
- Bei phase='persistent' am Ende genau eine Zusatzzeile: „Bei Persistenz/Progredienz: [__]“.
- Ausgabe als json: { "befunde_lueckentext": "string", "befunde_checkliste": ["string", "..."] }.
""".strip()

def generate_befunde_gaptext_german(anamnese_filled: str, humanize: bool = True, phase: str = "initial") -> Tuple[Dict[str, Any], str]:
    facts = _extract_explicit_facts(anamnese_filled)
    usr = {
        "anamnese_abgeschlossen": anamnese_filled,
        "phase": phase,
        "explizite_fakten": facts.get("facts_explicit"),
        "negationen_explizit": facts.get("negations_explicit"),
        "hinweise": "Nur ausfüllbare Struktur; keine subjektiven Symptome/Anamnese-Wiederholungen."
    }
    prefix = "Du bist medizinischer Assistent in einer Schweizer Hausarztpraxis.\n" + _swiss_style_note(humanize) + "\n" + ASSERTIONS_GUARD_BEFUNDE
    result = _ask_openai_json(build_messages([prefix, BEFUNDE_STEP_PROMPT], usr))
    bef_text = (result or {}).get("befunde_lueckentext", "").strip() if isinstance(result, dict) else ""

    def _bad(t: str) -> bool:
        if not t or t.startswith("- ") or "\n- " in t: return True
        if _has_unbracketed_measurements(t): return True
        if re.search(r"\((gut|mittel|reduziert|unauffällig|auffällig)\)$", t.lower(), re.M): return True  # vorgewählt am Zeilenende
        # verbiete subjektive Marker
        if re.search(r"\b(begleitsymptome|berichtet|klagt|symptome|seit|verlauf|nrs|charakter)\b", (t or "").lower()):
            return True
        return False

    if _bad(bef_text):
        strict = "Keine Bullets; KEINE voreingestellten Optionen; Zahlen nur als [__]; keine subjektiven/Anamnese-Zeilen."
        result2 = _ask_openai_json(build_messages([prefix, BEFUNDE_STEP_PROMPT], usr, strict))
        cand = (result2 or {}).get("befunde_lueckentext", "").strip() if isinstance(result2, dict) else ""
        if cand and not _bad(cand):
            result, bef_text = result2, cand

    if not bef_text or _bad(bef_text):
        lines = [
            "AZ: (gut/mittel/reduziert)",
            "Orientierung (zeitlich/situativ): (vollständig/teilweise/eingeschränkt)",
            "Vitalparameter: RR [__] mmHg, HF [__]/min, SpO2 [__]%, Temp [__] °C",
            "Inspektion: [__]",
            "Palpation/Druckdolenz (keine/leicht/stark); Lokalisation [__]",
            "Bewegung/Belastbarkeit: (frei/eingeschränkt) [__]",
            "Neurologischer Kurzstatus: [__]",
            "POCT/Labor (falls erhoben): CRP [__], BZ [__], Urinstix [__]",
            "EKG (falls erhoben): (unauffällig/auffällig); Befund [__]"
        ]
        if phase == "persistent":
            lines.append("Bei Persistenz/Progredienz: (Bildgebung/erweitertes Labor [__])")
        bef_text = "\n".join(lines)
        result = {"befunde_lueckentext": bef_text, "befunde_checkliste": lines}

    return result, bef_text

# ------------------ Zusatz: gezielte Untersuchungen / kurzer Status ------------------

SUGGEST_EXAMS_STEP_PROMPT = """
AUFGABE (Zusatz-Untersuchungen / kurzer Status):
- Schreibe 3–6 telegraphische Zeilen, die zur Anamnese passen (gezielter Status / Basisdiagnostik / POCT).
- Zahlen ausschliesslich als [__]; Optionen ohne Vorauswahl (z. B. (ja/nein [__]) oder (unauffällig/auffällig)).
- Keine subjektiven Symptome oder Anamnese-Wiederholungen.
- Schweizer Grundversorgung; pragmatisch, kein Overkill.
Beispiele:
- Thorax: Auskultation [__]; Perkussion [__]
- Abdomen: weich/gedrückt [__]; Abwehr (ja/nein [__])
- Rücken: Klopfschmerz (ja/nein [__]); Lasègue [__]
- Neurologischer Kurzstatus: Kraft/Sens [__], Reflexe [__]
- POCT: CRP [__], BZ [__], Urinstix [__]
""".strip()

def suggest_basic_exams_german(
    anamnese_filled: str,
    humanize: bool = True,
    phase: str = "initial"
) -> str:
    """
    Liefert einen kurzen, ausfüllbaren Untersuchungs-/Status-Block als Fliesstext.
    Signatur bleibt kompatibel zum UI-Import.
    """
    facts = _extract_explicit_facts(anamnese_filled)
    prefix = (
        "Du bist medizinischer Assistent in einer Schweizer Hausarztpraxis.\n"
        + _swiss_style_note(humanize)
        + "\n" + ASSERTIONS_GUARD_BEFUNDE
    )
    usr = {
        "anamnese_abgeschlossen": anamnese_filled,
        "phase": phase,
        "explizite_fakten": facts.get("facts_explicit"),
        "negationen_explizit": facts.get("negations_explicit"),
        "hinweise": "Nur objektive Untersuchungs-/Statuszeilen; Zahlen als [__]; keine Anamnese-Echos."
    }
    messages = build_messages([prefix, SUGGEST_EXAMS_STEP_PROMPT], usr)
    text = ask_openai_text(messages)

    # Safety: keine nackten Messwerte/Einheiten ausserhalb [__]
    if _has_unbracketed_measurements(text or ""):
        text = re.sub(r"\b\d[\d\.,]*\s*(mm\s*hg|mmhg|/min|bpm|%|°\s*c|°c|degc)\b", "[__]", text or "", flags=re.I)
    # Safety: subjektive Marker raus
    if re.search(r"\b(begleitsymptome|berichtet|klagt|symptome|seit|verlauf|nrs|charakter)\b", (text or "").lower()):
        lines = []
        for ln in (text or "").splitlines():
            if not re.search(r"\b(begleitsymptome|berichtet|klagt|symptome|seit|verlauf|nrs|charakter)\b", ln.lower()):
                lines.append(ln)
        text = "\n".join(lines).strip()

    return text.strip()


# ------------------ Schritt 3: Beurteilung + Prozedere ------------------

AP_STEP_PROMPT = """
AUFGABE:
- Liefere **Beurteilung** (1 Arbeitsdiagnose + 2–3 DD mit je 1 Begründung, falls plausibel).
- Und **Prozedere** (klare Zeilen: Massnahmen, Verlauf/Kontrolle, Warnzeichen, ggf. Abklärung/Überweisung).
FORMAT:
- CH-Deutsch, telegraphisch; keine Befundwiederholung; keine Spekulation.
- Antworte ausschliesslich als json: { "beurteilung_text": "string", "prozedere_text": "string" }.
""".strip()

def generate_assessment_and_plan_german(anamnese_final: str, befunde_final: str, humanize: bool = True, phase: str = "initial") -> Tuple[str, str]:
    try:
        red_flags_data = load_red_flags(RED_FLAGS_PATH)
        rf_hits = check_red_flags(anamnese_final + "\n" + befunde_final, red_flags_data, return_keywords=True) or []
        red_flags_list = [f"{kw} – {msg}" for (kw, msg) in rf_hits]
    except Exception:
        red_flags_list = []

    facts = _extract_explicit_facts(anamnese_final + "\n" + befunde_final)
    prefix = "Du bist medizinischer Assistent in einer Schweizer Hausarztpraxis.\n" + _swiss_style_note(humanize) + "\n" + ASSERTIONS_GUARD_AP
    usr = {
        "anamnese": anamnese_final, "befunde": befunde_final, "phase": phase,
        "red_flags": red_flags_list,
        "explizite_fakten": facts.get("facts_explicit"), "negationen_explizit": facts.get("negations_explicit"),
        "hinweise": "Fehlende Infos -> konditional formulieren."
    }
    result = _ask_openai_json(build_messages([prefix, AP_STEP_PROMPT], usr))
    beurteilung = (result or {}).get("beurteilung_text", "").strip() if isinstance(result, dict) else ""
    prozedere   = (result or {}).get("prozedere_text", "").strip() if isinstance(result, dict) else ""

    # End-Guards
    if _assertion_violations_generic(anamnese_final + "\n" + befunde_final, beurteilung):
        beurteilung = re.sub(r"\b\d[\d\.,]*\s*(mm\s*hg|mmhg|/min|bpm|%|°\s*c|°c|degc)\b", "[__]", beurteilung, flags=re.I)
        beurteilung = re.sub(r"\b(keine|ohne|nicht)\b[^\.]*", "Unklar – klinisch verifizieren.", beurteilung)
    if _assertion_violations_generic(anamnese_final + "\n" + befunde_final, prozedere):
        prozedere = re.sub(r"\b\d[\d\.,]*\s*(mm\s*hg|mmhg|/min|bpm|%|°\s*c|°c|degc)\b", "[__]", prozedere, flags=re.I)

    return beurteilung, prozedere

def build_messages(prefix_blocks: List[str], user_payload: dict, strict_note: str = "") -> List[Dict[str, str]]:
    sys = "\n\n".join([b.strip() for b in prefix_blocks if b and b.strip()])
    if strict_note:
        sys += "\n\nZUSATZ – STRIKT:\n" + strict_note.strip()
    sys += "\n\nFORMAT-HINWEIS: Antworte nur als json-Objekt (keine Prosa)."
    return [
        {"role": "system", "content": sys},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
