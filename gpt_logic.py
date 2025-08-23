# gpt_logic.py
"""
Psychologie-fokussierte Logik für den Praxis-Assistenten
- Saubere Trennung Logik/UI (keine tkinter-Referenzen)
- Zentraler Resolver für Red-Flag-Dateien (Psychologie bevorzugt)
- Schweizer Orthografie, AUSFÜHRLICHER Erstbericht (keine Telegraphie)
- Funktionen, die vom UI genutzt werden:
    * resolve_red_flags_path()
    * generate_full_entries_german()
    * generate_status_gaptext_german()
    * generate_assessment_and_plan_german()
    * compose_erstbericht()
    * reset_openai_client()
    * explain_plan_brief()
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from openai import OpenAI
from red_flags_checker import check_red_flags, load_red_flags

# Kanonische Top-Level-Überschriften (für UI/Endausgabe)
TOP_HEADS = [
    "Eintrittssituation",
    "Aktuelle Anamnese",
    "Hintergrundanamnese",
    "Soziale Anamnese",
    "Familiäre Anamnese",
    "Psychostatus (heute)",
    "Suizidalität & Risikoeinschätzung",
    "Einschätzung",
    "Prozedere",
    # Legacy-Container (werden gelegentlich als Roh-Headings eingegeben)
    "Anamnese",
    "Status",
]


def _top_heads_regex() -> str:
    alts = [
        r"Eintrittssituation",
        r"Aktuelle Anamnese(?:\s*\(inkl\. Suizidalität, falls erwähnt\))?",
        r"Hintergrundanamnese",
        r"Soziale Anamnese",
        r"Famili(?:ä|a)re Anamnese|Familienanamnese",
        r"Psychostatus(?:\s*\(heute\))?|Psychischer Status|Psychopathologischer Status",
        r"Suizidalität\s*(?:&|und)\s*Risikoeinsch(?:ä|ae)tzung",
        r"Einschätzung",
        r"Prozedere",
        r"Anamnese",
        r"Status",
    ]
    return "|".join(f"(?:{p})" for p in alts)

# ------------------ Logging ------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------ Config & Client ------------------
MODEL_DEFAULT = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

_client: Optional[OpenAI] = None

HEADINGS_INLINE = False
HEADING_SPACES = 3


def _get_openai_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise EnvironmentError("❌ Umgebungsvariable OPENAI_API_KEY ist nicht gesetzt!")
        _client = OpenAI(api_key=api_key)
    return _client


def reset_openai_client() -> None:
    global _client
    _client = None

# ------------------ Pfade & Resolver ------------------
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
RED_FLAGS_PATH = os.path.join(THIS_DIR, "red_flags.json")
PSYCH_RED_FLAGS_PATH = os.path.join(THIS_DIR, "psych_red_flags.json")

APP_MODE = os.getenv("APP_MODE", "psychology").lower()


def resolve_red_flags_path(prefer_psych: bool = True) -> str:
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

def _rectify_assessment_vs_plan(einsch: str, proz: str) -> tuple[str, str]:
    if not proz:
        return einsch, proz

    plan_heads = tuple(x.lower() for x in [
        "setting", "intervention", "interventionen", "hausaufgaben", "arbeitsauftrag",
        "sicherheit", "krisenplan", "notfallkontakte", "einbezug", "koordination",
        "verlauf", "kontrolle", "screening", "diagnostik", "medikation", "medikamente",
    ])
    assess_kw = re.compile(r"\b(hypothese|alternativhypothese|fallformulierung|komorbid|"
                           r"schweregrad|dringlichkeit|differenzial|dd|risikoeinschätzung)\b",
                           flags=re.IGNORECASE)

    moved: list[str] = []
    kept: list[str] = []

    for raw in proz.splitlines():
        ln = raw.strip()
        if not ln:
            kept.append(raw); continue

        is_bullet = bool(re.match(r"^\s*[-•–]\s+", ln))
        starts_like_plan = ln.lower().startswith(plan_heads)

        if is_bullet or starts_like_plan:
            kept.append(raw); continue

        if assess_kw.search(ln):
            moved.append(ln); continue

        moved.append(ln)

    new_einsch = (einsch.strip() + (("\n\n" + "\n".join(moved).strip()) if moved else "")).strip()
    new_proz   = "\n".join(kept).strip()
    return new_einsch, new_proz


def explain_plan_brief(
    plan_text: str,
    *,
    anamnese: str = "",
    status: str = "",
    einschaetzung: str = "",
    max_words: int = 12,
) -> str:
    plan = (plan_text or "").strip()
    if not plan:
        return plan

    sys_msg = (
        "Du bist erfahrener Psychiater in einer Schweizer Praxis. "
        "Aufgabe: Nimm den gegebenen Plan-Text (Bullets/Unter-Bullets) und füge "
        "an JEDE Unter-Bullet-Zeile eine kurze Begründung an, z.B. ' – warum: Schlaf stabilisieren'. "
        f"Max {max_words} Wörter pro Begründung. "
        "WICHTIG: Struktur UNVERÄNDERT lassen (gleiche Zeilen, gleiche Reihenfolge), "
        "keine neuen Titel/Abschnitte einfügen, nichts löschen, keine Doppelpunkte nach Abschnittstiteln. "
        "Gib NUR den transformierten Plan-Text zurück."
    )
    usr = {
        "plan": plan,
        "kontext": {
            "anamnese": anamnese,
            "status": status,
            "einschaetzung": einschaetzung,
        },
    }
    try:
        out = ask_openai(sys_msg + "\n\n" + json.dumps(usr, ensure_ascii=False))
        out = (out or "").strip()
        return out or plan
    except Exception:
        return plan


def _extract_block(text: str, head: str) -> str:
    if not text:
        return ""
    heads = _top_heads_regex()
    pat_head = rf"(?im)^\s*({head})\s*:?\s*$"
    pat_any  = rf"(?im)^\s*(?:{heads})\s*:?\s*$"

    s = text.replace("\r\n", "\n")
    m = re.search(pat_head, s)
    if not m:
        return ""
    start = m.end()
    m2 = re.search(pat_any, s[start:])
    end = start + (m2.start() if m2 else len(s))
    return s[start:end].strip()


def categorize_anamnese_with_llm(free_text: str, status_hint: str = "") -> Dict[str, str]:
    """
    Nutzt das LLM, um Freitext in fünf Abschnitte zu verteilen und paraphrasiert zu formulieren.
    Status wird nur als Kontext verwendet (keine 1:1-Übernahme in die Anamnese).
    Rückgabe-Keys: eintritt, aktuell, hintergrund, sozial, familiaer
    """
    sys_msg = (
        "Du bist leitender Psychiater in einer Schweizer Ambulanz. "
        "Zerlege den Patiententext in fünf Anamneseabschnitte und paraphrasiere klinisch präzise, "
        "in vollständigen Sätzen, ohne Fragen/Listenstil. Schweizer Orthografie (ss). "
        "Nutze Angaben aus dem Psychostatus nur zur Kontextualisierung (z. B. Verlauf, Glaubhaftigkeit), "
        "übernimm aber keine Status-Beobachtungen in die Anamnese. Fehlende Informationen -> 'keine Angaben'."
    )

    user_payload = {
        "freitext": _strip_question_lines(free_text or ""),
        "status_kontext": (status_hint or ""),
        "abschnitte": [
            "Eintrittssituation",
            "Aktuelle Anamnese",
            "Hintergrundanamnese",
            "Soziale Anamnese",
            "Familiäre Anamnese",
        ],
        "form": "volle Sätze, keine Bulletpoints, klinisch neutral",
        "ausgabe_json_schema": {
            "eintritt": "string",
            "aktuell": "string",
            "hintergrund": "string",
            "sozial": "string",
            "familiaer": "string",
        }
    }

    result = _ask_openai_json(
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
    )

    if not isinstance(result, dict):
        result = {}

    def clean(x: str) -> str:
        x = (x or "").strip()
        x = re.sub(r"\s+\n", "\n", x)
        return x if x else "keine Angaben"

    return {
        "eintritt":   clean(result.get("eintritt", "")),
        "aktuell":    clean(result.get("aktuell", "")),
        "hintergrund":clean(result.get("hintergrund", "")),
        "sozial":     clean(result.get("sozial", "")),
        "familiaer":  clean(result.get("familiaer", "")),
    }


def _build_anamnese_from_sections(sec: Dict[str, str]) -> str:
    parts = [
        "Eintrittssituation\n" + (sec.get("eintritt") or "keine Angaben"),
        "Aktuelle Anamnese\n" + (sec.get("aktuell") or "keine Angaben"),
        "Hintergrundanamnese\n" + (sec.get("hintergrund") or "keine Angaben"),
        "Soziale Anamnese\n" + (sec.get("sozial") or "keine Angaben"),
        "Familiäre Anamnese\n" + (sec.get("familiaer") or "keine Angaben"),
    ]
    return "\n\n".join(parts).strip()


# --- Prozedere: Gruppierung & Planbau ---------------------------------------

SECTION_ORDER = [
    "Setting & Ziele",
    "Psychoedukation",
    "Aktivitätsaufbau",
    "Schlaf & Rhythmus",
    "Angst/Skills/Exposition",
    "Substanzkonsum",
    "Sicherheit/Krisenplan",
    "Einbezug/Koordination",
    "Diagnostik/Screenings",
    "Verlauf/Monitoring",
    "Arbeit & Soziales",
    "Medikation (Koordination)",
]

SECTION_PATTERNS = {
    "Setting & Ziele":           r"\b(setting|ziel|vereinbar|bündnis|allianz)\b",
    "Psychoedukation":           r"\b(psychoeduk)\w*\b",
    "Aktivitätsaufbau":          r"\b(aktivitäts|aktivierung|verhaltensaktiv|angenehme aktiv)\b",
    "Schlaf & Rhythmus":         r"\b(schlaf|hygiene|rhythmus|aufstehzeit|abendroutine)\b",
    "Angst/Skills/Exposition":   r"\b(angst|exposition|skills?|atmen|atem|bodyscan|entspann|umstrukturier|kognitiv)\b",
    "Substanzkonsum":            r"\b(alkohol|substanz|konsum|abstinenz|reduktion|trinken)\b",
    "Sicherheit/Krisenplan":     r"\b(krisen|sicherheits?|notfall|frühwarn|suizid|fremdgefähr)\b",
    "Einbezug/Koordination":     r"\b(einbezug|angehörig|hausarzt|psychiatr|koordination)\b",
    "Diagnostik/Screenings":     r"\b(screen|diagnostik|fragebogen|phq|gad|test)\b",
    "Verlauf/Monitoring":        r"\b(verlauf|monitor|skala|tagebuch|hausaufgabe|termin|review|evaluation)\b",
    "Arbeit & Soziales":         r"\b(arbeit|job|krankmeldung|iv|re-?integration|sozial)\b",
    "Medikation (Koordination)": r"\b(medik|ärzt|somat|apothek)\b",
}


def _is_already_grouped(proz: str) -> bool:
    if not proz:
        return False
    return sum(1 for ln in proz.splitlines() if re.match(r"^\s{2,}-\s+", ln.strip(" "))) >= 2


def _ensure_grouped_plan(proz: str) -> str:
    if not proz:
        return ""
    if _is_already_grouped(proz):
        return proz

    proz = re.sub(r"(?im)^\s*behandlungsplan\s*$", "", proz).strip()

    bullets = []
    for ln in proz.replace("\r\n", "\n").split("\n"):
        m = re.match(r"^\s*[-•–]\s+(.*\S)\s*$", ln)
        if m:
            bullets.append(m.group(1).strip())

    if not bullets:
        return proz.strip()

    buckets = {sec: [] for sec in SECTION_ORDER}
    other = []
    for item in bullets:
        placed = False
        for sec, pat in SECTION_PATTERNS.items():
            if re.search(pat, item, flags=re.IGNORECASE):
                if item.lower() != sec.lower():
                    buckets[sec].append(item)
                placed = True
                break
        if not placed:
            other.append(item)

    if other:
        for x in other:
            tgt = "Verlauf/Monitoring" if re.search(r"\b(termin|kontroll|review|evaluation)\b", x, re.IGNORECASE) else "Setting & Ziele"
            buckets[tgt].append(x)

    out_lines = []
    for sec in SECTION_ORDER:
        items = []
        seen = set()
        for it in buckets[sec]:
            t = it.strip().rstrip(".")
            if not t or t.lower() == sec.lower():
                continue
            if t in seen:
                continue
            seen.add(t); items.append(t)

        if items:
            out_lines.append(f"- {sec}")
            for it in items:
                out_lines.append(f"  - {it}")
    return "\n".join(out_lines).strip()


def _split_snippets(text: str) -> list[str]:
    if not text:
        return []
    s = text.replace("\r\n", "\n")
    chunks = re.split(r"[;\n]+", s)
    out = []
    for ch in chunks:
        ch = ch.strip(" -•–\t")
        if not ch:
            continue
        parts = re.split(r"(?<=[.!?])\s+", ch)
        for p in parts:
            p = p.strip()
            if p:
                out.append(p)
    return out


def _route_snippets_to_sections(text: str) -> Dict[str, list[str]]:
    snips = _split_snippets(_strip_question_lines(text))
    buckets = {
        "Eintrittssituation": [],
        "Aktuelle Anamnese": [],
        "Hintergrundanamnese": [],
        "Soziale Anamnese": [],
        "Familiäre Anamnese": [],
    }
    for s in snips:
        low = s.lower()

        if re.search(r"\b(heute|jetzt|erst(konsultation)?|vorstellung|notfall|zugewiesen|überwiesen)\b", low):
            buckets["Eintrittssituation"].append(s);  continue

        if re.search(r"\b(suizid|suizidgedanken|selbstverletz|zwang|angst|panik|depress|schlaf|appetit|stress|belast|auslöser|tod|verlust|hund|alkohol|cannabis|substanz|heftiger geworden|seit [0-9]+ (tagen|wochen|monaten))\b", low):
            buckets["Aktuelle Anamnese"].append(s);   continue

        if re.search(r"\b(früher|seit kindheit|vorgeschichte|früher(e|en)? behandl|diagnos|rezidiv|stationär|ambulant)\b", low):
            buckets["Hintergrundanamnese"].append(s); continue

        if re.search(r"\b(arbeit|job|stelle|schicht|schul|studium|beziehung|partner|freund|freunde|soziale|wohnen|allein|mit|unterstützung|netz)\b", low):
            buckets["Soziale Anamnese"].append(s);    continue

        if re.search(r"\b(mutter|vater|schwester|bruder|tochter|sohn|familie|familiär|genet|angehörig|nichte|neffe)\b", low):
            buckets["Familiäre Anamnese"].append(s);  continue

        buckets["Aktuelle Anamnese"].append(s)

    for k in buckets:
        seen = set()
        uniq = []
        for x in buckets[k]:
            if x not in seen:
                seen.add(x); uniq.append(x)
        buckets[k] = uniq
    return buckets


def _paraphrase_bucket_llm(heading: str, raw_text: str) -> str:
    if not raw_text.strip():
        return "keine Angaben"
    try:
        sys_msg = (
            "Du bist erfahrener Psychologe in einer Schweizer Praxis. "
            "Formuliere den folgenden Notiz-/Stichwort-Text kurz um: 2–4 Sätze, "
            "3. Person (die Patientin/der Patient), klinisch-sachlich, präzise, CH-Orthografie. "
            "Keine neuen Informationen erfinden, keine Listen, keine Zitate."
        )
        usr = {"abschnitt": heading, "rohtext": raw_text}
        txt = ask_openai(sys_msg + "\n\n" + json.dumps(usr, ensure_ascii=False))
        txt = " ".join(x.strip() for x in (txt or "").splitlines() if x.strip())
        return txt or raw_text.strip()
    except Exception:
        return re.sub(r"\s+", " ", raw_text).strip()


def _build_anamnese_from_router(source_text: str) -> str:
    clean = _strip_top_level_headings(_strip_question_lines(source_text))
    buckets = _route_snippets_to_sections(clean)

    def _join_bucket(key: str) -> str:
        xs = buckets.get(key, []) or []
        return " ".join(xs).strip()

    sections = [
        ("Eintrittssituation", _join_bucket("Eintrittssituation")),
        ("Aktuelle Anamnese", _join_bucket("Aktuelle Anamnese")),
        ("Hintergrundanamnese", _join_bucket("Hintergrundanamnese")),
        ("Soziale Anamnese", _join_bucket("Soziale Anamnese")),
        ("Familiäre Anamnese", _join_bucket("Familiäre Anamnese")),
    ]

    blocks = []
    for heading, raw in sections:
        para = _paraphrase_bucket_llm(heading, raw) if raw else "keine Angaben"
        blocks.append(f"{heading}\n{para}")

    return "\n\n".join(blocks).strip()


def _strip_question_lines(text: str) -> str:
    if not text:
        return ""
    s = text.replace("\r\n", "\n")
    s = re.sub(r"\?n(\s|$)", "?\n", s)
    out = []
    for ln in s.splitlines():
        if re.match(r"^\s*-\s+.+\?\s*$", ln):
            continue
        if re.match(r"^\s*.+\?\s*$", ln):
            continue
        out.append(ln)
    return "\n".join(out).strip()


def _strip_top_level_headings(text: str) -> str:
    if not text:
        return ""
    s = text.replace("\r\n", "\n")
    heads = _top_heads_regex()
    s = re.sub(rf"(?im)^\s*(?:{heads})\s*:?\s*$", "", s)
    s = re.sub(rf"(?im)^\s*(?:{heads})\s*:\s*", "", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _strip_heading_prefix(block: str, heading: str) -> str:
    if not block:
        return ""
    pat = rf"(?im)^\s*\*{{0,2}}{re.escape(heading)}\*{{0,2}}\s*:?\s*$"
    return re.sub(pat, "", block).strip()


def _fallback_rebuild_anamnese(clean_text: str) -> str:
    b = _route_snippets_to_sections(clean_text)
    def join_or_none(xs: list[str]) -> str:
        return " ".join(xs).strip() if xs else "keine Angaben"
    return "\n\n".join([
        "Eintrittssituation\n" + join_or_none(b.get("Eintrittssituation", [])),
        "Aktuelle Anamnese\n" + join_or_none(b.get("Aktuelle Anamnese", [])),
        "Hintergrundanamnese\n" + join_or_none(b.get("Hintergrundanamnese", [])),
        "Soziale Anamnese\n" + join_or_none(b.get("Soziale Anamnese", [])),
        "Familiäre Anamnese\n" + join_or_none(b.get("Familiäre Anamnese", [])),
    ]).strip()



def ask_openai(prompt: str) -> str:
    resp = _get_openai_client().chat.completions.create(
        model=MODEL_DEFAULT,
        messages=[
            {"role": "system", "content": "Antworte ausschliesslich auf Deutsch. Differenziert, präzise, praxisnah."},
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


def swiss_erstbericht_style(humanize: bool = True) -> str:
    base = (
        "Schweizer Orthografie (ss statt ß). "
        "Ausführlicher, detaillierter Erstbericht in vollständigen Sätzen; klinisch-sachlich, präzise, ohne Floskeln. "
        "Klare Gliederung: Eintrittssituation; Aktuelle Anamnese; Hintergrundanamnese; "
        "Soziale Anamnese; Familiäre Anamnese; Psychostatus (heute); Suizidalität inkl. glaubhafter Erklärung & Risikoeinschätzung; "
        "Differenzierte Einschätzung (Distanzierungs-/Absprache-/Bündnisfähigkeit integrieren); Prozedere. "
        "Wörtliche Patienten-Zitate sparsam einstreuen und mit «…» kennzeichnen. "
        "Keine Aufzählungslisten, ausser im Abschnitt «Prozedere»."
    )
    if humanize:
        base += " Ton: menschlich, respektvoll, aber fachlich."
    return base


def _format_full_entries_block(payload: Dict[str, Any]) -> str:
    parts: List[str] = []
    parts.append("Anamnese")
    parts.append((payload.get("anamnese_text") or "keine Angaben").strip())
    parts.append("")
    parts.append("Status")
    parts.append((payload.get("status_text") or "keine Angaben").strip())
    parts.append("")
    parts.append("Einschätzung")
    parts.append((payload.get("beurteilung_text") or "keine Angaben").strip())
    parts.append("")
    parts.append("Prozedere")
    parts.append((payload.get("prozedere_text") or "keine Angaben").strip())
    txt = "\n".join(parts).strip()
    return _normalize_headings_to_spaces(txt)


def compose_erstbericht(payload: Dict[str, Any]) -> str:
    ana = (payload.get("anamnese_text") or "").strip()
    status = (payload.get("status_text") or "").strip()
    beurteilung = (payload.get("beurteilung_text") or "").strip()
    proz = (payload.get("prozedere_text") or "").strip()

    parts: List[str] = []
    if ana:
        parts.append(ana)
    if status:
        parts.append(status)
    parts.append("Einschätzung\n" + (beurteilung if beurteilung else "keine Angaben"))
    parts.append("Prozedere\n" + (proz if proz else "keine Angaben"))

    report = "\n\n".join(parts).strip()
    return _normalize_headings_to_spaces(report)


def _normalize_headings_to_spaces(text: str, spaces: int = HEADING_SPACES) -> str:
    if not text:
        return ""
    heads_alt = _top_heads_regex()
    S = " " * max(1, spaces)

    s = text.replace("\r\n", "\n")

    if not HEADINGS_INLINE:
        s = re.sub(fr"(?im)^\s*({heads_alt})\s*:\s*(.+)$", r"\1\n\2", s)
        s = re.sub(fr"(?im)^\s*({heads_alt})\s{{2,}}(.+)$", r"\1\n\2", s)
        lines = s.split("\n")
        out = []
        i = 0
        while i < len(lines):
            ln = lines[i]
            m = re.match(fr"^\s*({heads_alt})\s*:?\s$", ln, flags=re.IGNORECASE)
            if m:
                head = m.group(1)
                j = i + 1
                while j < len(lines) and lines[j].strip() == "":
                    j += 1
                if j < len(lines) and not re.match(fr"^\s*(?:{heads_alt})\s*:?\s$", lines[j], re.IGNORECASE):
                    out.append(head)
                    out.append(lines[j].strip())
                    i = j + 1
                else:
                    out.append(head)
                    i += 1
            else:
                out.append(ln)
                i += 1
        s = "\n".join(out)
        s = re.sub(r"\n{3,}", "\n\n", s).strip()
        return s

    s = re.sub(fr"(?im)^\s*({heads_alt})\s*:\s*(.+)$", rf"\1{S}\2", s)
    lines = s.split("\n")
    out = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(fr"^\s*({heads_alt})\s*:?\s$", ln, flags=re.IGNORECASE)
        if m:
            head = m.group(1)
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines) and not re.match(fr"^\s*(?:{heads_alt})\s*:?\s$", lines[j], re.IGNORECASE):
                out.append(f"{head}{S}{lines[j].strip()}")
                i = j + 1
            else:
                out.append(head)
                i += 1
        else:
            out.append(ln)
            i += 1
    s = "\n".join(out)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s

# ------------------ 4 Felder – Psychologie ------------------

def _enforce_psych_erstgespraech_layout(payload: Dict[str, Any]) -> Dict[str, Any]:
    import re

    CANON = [
        ("Eintrittssituation", r"eintrittssituation"),
        ("Aktuelle Anamnese", r"aktuelle\s+anamnese(?:\s*\(.*?\))?"),
        ("Hintergrundanamnese", r"hintergrund\s*anamnese|hintergrundanamnese"),
        ("Soziale Anamnese", r"soziale\s+anamnese"),
        ("Familiäre Anamnese", r"famili(?:ä|a)re\s+anamnese|familienanamnese"),
    ]

    def _split_by_heads(text: str) -> Dict[str, str]:
        text = (text or "").strip()
        if not text:
            return {}
        head_pat = r"(?im)^(?P<h>(?:{alts}))\s*:?\s$".format(
            alts="|".join(f"(?:{pat})" for _, pat in CANON)
        )
        parts: Dict[str, str] = {}
        matches = list(re.finditer(head_pat, text))
        if not matches:
            return {}

        def _canon_label(mh: str) -> str:
            h = mh.strip().lower()
            for label, pat in CANON:
                if re.fullmatch(pat, h, flags=re.IGNORECASE):
                    return label
            return mh

        for i, m in enumerate(matches):
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            raw = text[start:end].strip()
            raw = re.sub(head_pat, "", raw).strip()
            raw = re.sub(r"\n{3,}", "\n\n", raw).strip()
            label = _canon_label(m.group("h"))
            if label not in parts or (len(raw) > len(parts[label])):
                parts[label] = raw
        return parts

    anamnese_in = payload.get("anamnese_text", "") or ""
    lead_txt = ""
    m_first = re.search(r"(?im)^(eintrittssituation|aktuelle\s+anamnese(?:\s*\(.*?\))?|hintergrund\s*anamnese|hintergrundanamnese|soziale\s+anamnese|famili(?:ä|a)re\s+anamnese|familienanamnese)\s*:?\s$", anamnese_in)
    if m_first and m_first.start() > 0:
        lead_txt = _strip_question_lines(anamnese_in[:m_first.start()].strip())

    found = _split_by_heads(anamnese_in)
    rebuilt: List[str] = []
    for label, _ in CANON:
        content = (found.get(label) or "").strip()
        content = re.sub(
            r"(?im)^(eintrittssituation|aktuelle\s+anamnese(?:\s*\(.*?\))?|hintergrund\s*anamnese|hintergrundanamnese|soziale\s+anamnese|famili(?:ä|a)re\s+anamnese|familienanamnese)\s*:?\s$",
            "", content
        ).strip()

        if lead_txt:
            if label.startswith("Eintrittssituation") and not content:
                content = lead_txt; lead_txt = ""
            elif label.startswith("Aktuelle Anamnese") and not content:
                content = lead_txt; lead_txt = ""

        if not content:
            content = "keine Angaben"
        rebuilt.append(f"{label}\n{content}")

    payload["anamnese_text"] = "\n\n".join(rebuilt).strip()

    status = (payload.get("status_text") or "").strip()
    if status and not status.lower().startswith("psychostatus"):
        status = f"Psychostatus (heute)\n{status}".strip()

    risk_pat = r"(?im)^\s*Suizidalität\s*(?:&|und)\s*Risikoeinsch(?:ä|ae)tzung\s$"
    need_risk_head = not re.search(risk_pat, status)

    def _has_tok(pat: str) -> bool:
        return re.search(pat, status, flags=re.IGNORECASE) is not None

    dist = "Distanzierungsfähigkeit" if _has_tok(r"distanzierungsf(?:ä|ae)higkeit") else "Distanzierungsfähigkeit: unklar"
    abspr = "Absprachefähigkeit"     if _has_tok(r"absprachef(?:ä|ae)higkeit")     else "Absprachefähigkeit: unklar"
    buend = "Bündnisfähigkeit"       if _has_tok(r"b[üu]ndnisf(?:ä|ae)higkeit")    else "Bündnisfähigkeit: unklar"

    if need_risk_head:
        status = f"{status}\n\nSuizidalität & Risikoeinschätzung\n{dist} | {abspr} | {buend}".strip()

    payload["status_text"] = status
    return payload


def generate_full_entries_german(
    user_input: str, context: Optional[Dict[str, Any]] = None
) -> Tuple[Dict[str, str], str]:
    context = context or {}
    try:
        path = resolve_red_flags_path(prefer_psych=True)
        red_flags_data = load_red_flags(path)
        rf_hits = check_red_flags(user_input, red_flags_data, return_keywords=True) or []
        red_flags_list = [f"{kw} – {msg}" for (kw, msg) in rf_hits]
    except Exception:
        red_flags_list = []

    style = swiss_erstbericht_style(humanize=True)
    sys_msg = (
        "Du bist erfahrener Psychiater/Psychologe in einer Schweizer Praxis (ambulantes Erstgespräch).\n"
        + style + "\n"
        "WICHTIG:\n"
        "- Nichts erfinden. Wo Angaben fehlen: «keine Angaben», «nicht erhoben» oder «noch ausstehend».\n"
        "- Re-Ordnung: Egal in welcher Reihenfolge der Input kommt – Anamnese **immer** gliedern als Absätze in genau dieser Reihenfolge mit genau diesen Überschriften:\n"
        "  1) Eintrittssituation \n"
        "  2) Aktuelle Anamnese \n"
        "  3) Hintergrundanamnese \n"
        "  4) Soziale Anamnese \n"
        "  5) Familiäre Anamnese \n"
        "- Status: **Psychostatus (heute)** als Absätze: Erscheinung/Verhalten; Sprache/Denken; Stimmung/Affekt; Wahrnehmung; Kognition/Orientierung.\n"
        "- Risiko: Abschnitt «Suizidalität & Risikoeinschätzung» explizit ausweisen (ja/nein/unklar) inkl. glaubhafter Erklärung zu **Distanzierungsfähigkeit**, **Absprachefähigkeit**, **Bündnisfähigkeit** kurz einordnen.\n"
        "- Inhaltliche Regel: Wenn Suizidalität erwähnt wird, **integriere sie inhaltlich** in die «Aktuelle Anamnese», zusätzlich einen separaten Risiko-Abschnitt «Suizidalität & Risikoeinschätzung» anlegen.\n"
        "- Einschätzung: differenzierte, ausführliche, psychologische Fallformulierung (Auslöser/aufrechterhaltende Faktoren/Ressourcen, Funktionsniveau) + Schweregrad (leicht/mittel/schwer) + Dringlichkeit (niedrig/mittel/hoch). 2–4 Alternativhypothesen, falls sinnvoll.\n"
        "- Prozedere: klare Bulletpoints (Interventionen, Sicherheit/Krisenplan, Einbezug Dritter nach Einwilligung, Verlauf/Kontrolle, Koordination; Medikation nur allgemein, ohne Dosierungen).\n"
        "- Antworte **ausschliesslich** als JSON:\n"
        "{\n"
        "  \"anamnese_text\": \"Eintrittssituation\\n...\\n\\nAktuelle Anamnese\\n...\\n\\nHintergrundanamnese\\n...\\n\\nSoziale Anamnese\\n...\\n\\nFamiliäre Anamnese\\n...\",\n"
        "  \"status_text\": \"Psychostatus (heute)\\nErscheinung/Verhalten ...\\nSprache/Denken ...\\nStimmung/Affekt ...\\nWahrnehmung ...\\nKognition/Orientierung ...\\n\\nSuizidalität & Risikoeinschätzung\\nDistanzierungsfähigkeit: ja/nein/unklar | Absprachefähigkeit: ja/nein/unklar | Bündnisfähigkeit: ja/nein/unklar\",\n"
        "  \"beurteilung_text\": \"...\",\n"
        "  \"prozedere_text\": \"- ...\\n- ...\"\n"
        "}\n"
    ).strip()

    raw_anamnese = _extract_block(user_input, "Anamnese") or user_input
    raw_status   = _extract_block(user_input, "Status")   or ""

    clean_anamnese = _strip_top_level_headings(_strip_question_lines(raw_anamnese))
    clean_status   = _strip_top_level_headings(raw_status)

    sec = categorize_anamnese_with_llm(free_text=clean_anamnese, status_hint=clean_status)
    anamnese_struct = _build_anamnese_from_sections(sec)

    anamnese_router_text = _build_anamnese_from_router(clean_anamnese)

    result = _ask_openai_json(
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": json.dumps({"eingabetext": clean_anamnese + ("\n\nStatus\n" + clean_status if clean_status else ""), "kontext": context}, ensure_ascii=False)},
        ]
    )

    if isinstance(result, dict):
        result = _enforce_psych_erstgespraech_layout(result)
        result["anamnese_text"] = anamnese_struct
        if red_flags_list:
            result["red_flags"] = red_flags_list

    full_block = _format_full_entries_block(result if isinstance(result, dict) else {})
    return result, full_block


# ------------------ Psychopathologischer Befund (Lückentext) ------------------

def generate_status_gaptext_german(
    anamnese_filled: str,
    humanize: bool = True,
    phase: str = "initial",
) -> Tuple[Dict[str, Any], str]:
    sys_msg = (
        "Du bist erfahrener Psychologe in einer Schweizer Praxis.\n"
        "Erzeuge eine strukturierte Liste **psychologischer Explorationspunkte** passend zur Anamnese. "
        "Kein fertiger Fliesstext, sondern ausfüllbare Punkte mit Platzhaltern/Optionen. "
        "Abdecken: Erscheinung/Verhalten; Stimmung/Affekt; Antrieb/Psychomotorik; "
        "Denken (Form/Inhalt); Wahrnehmung; Kognition/Orientierung/Aufmerksamkeit; Insight/Motivation; "
        "Substanzkonsum; Funktionsniveau; Ressourcen/Schutzfaktoren; **Risikoabschätzung** (Suizidalität/Fremdgefährdung inkl. glaubhafter Erklärung + Schutzfaktoren). "
        "Antworte ausschliesslich als JSON:\n{\n  \"status_lueckentext\": \"string\",\n  \"status_checkliste\": [\"string\"]\n}\n"
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

    if not bef_text:
        lines = [
            "- Erscheinung/Verhalten: gepflegt / ungepflegt / auffällig; Kontakt: gut / distanziert / vermeidend",
            "- Stimmung/Affekt: euthym / gedrückt / ängstlich; Affekt: stabil / labil / verflacht",
            "- Antrieb/Psychomotorik: normal / vermindert / gesteigert",
            "- Denken – Form/Inhalt: geordnet / Umständlichkeit / Grübeln / Zwangsgedanken; Selbst-/Fremdabwertung: ja/nein",
            "- Wahrnehmung: Halluzinationen/Entfremdung: ja/nein/unklar",
            "- Kognition/Orientierung/Aufmerksamkeit: intakt / leichte Defizite (kurz angeben: __ )",
            "- Insight/Motivation: gut / eingeschränkt; Ziele/Erwartungen: __",
            "- Substanzkonsum: Art/Menge/Frequenz: __ ; zuletzt: __",
            "- Funktionsniveau: Arbeit/Schule: __ ; Beziehungen: __ ; Schlaf: __ ; Alltagsbewältigung: __",
            "- Ressourcen/Schutzfaktoren: __",
            "- Risikoabschätzung: Suizidalität: ja/nein/unklar inkl. glaubhafter Erklärung (Akutplan? __); Fremdgefährdung: ja/nein/unklar; Schutzfaktoren: __",
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
    try:
        path = resolve_red_flags_path(prefer_psych=True)
        red_flags_data = load_red_flags(path)
        rf_hits = check_red_flags(anamnese_final + "\n" + status_final, red_flags_data, return_keywords=True) or []
        red_flags_list = [f"{kw} – {msg}" for (kw, msg) in rf_hits]
    except Exception:
        red_flags_list = []

    sys_part = (
        "Du bist erfahrener Psychologe in einer Schweizer Praxis (ambulante Erstkonsultation).\n"
        + swiss_erstbericht_style(humanize=humanize) + "\n"
        "Nur notwendige Infos; keine Wiederholungen von bereits Gesagtem. Schweizer/Europäische Good Practice priorisieren."
    ).strip()

    usr = {
        "anamnese": anamnese_final,
        "status": status_final,
        "phase": phase,
        "red_flags": red_flags_list,
    }

    json_spec = (
        "Antworte ausschliesslich als JSON-Objekt mit genau diesen Schlüsseln:\n"
        "{\n"
        "  \"einschaetzung_text\": \"string\",\n"
        "  \"prozedere_text\": \"string\"\n"
        "}\n"
        "- Strikte Trennung: Begründungen/Hypothesen/Dringlichkeit NUR in \"einschaetzung_text\".\n"
        "- In \"prozedere_text\" NUR umsetzbare Schritte, als Bulletliste.\n"
        "- Gliedere das Prozedere in Abschnitte (Bullet als Abschnittstitel, KEINE Doppelpunkte), darunter 2–4 Unter-Bullets. \n"
        "  - Setting & Ziele\n"
        "    - ...\n"
        "  - Psychoedukation\n"
        "    - ...\n"
        "  - Aktivitätsaufbau\n"
        "    - ...\n"
        "  - Schlaf & Rhythmus\n"
        "    - ...\n"
        "  - Angst/Skills/Exposition\n"
        "    - ...\n"
        "  - Substanzkonsum (falls relevant)\n"
        "    - ...\n"
        "  - Sicherheit/Krisenplan (falls relevant)\n"
        "    - ...\n"
        "  - Einbezug/Koordination\n"
        "    - ...\n"
        "  - Diagnostik/Screenings (indikationsbezogen)\n"
        "    - ...\n"
        "  - Verlauf/Monitoring\n"
        "    - ...\n"
        "  - Arbeit & Soziales (falls relevant)\n"
        "    - ...\n"
        "  - Medikation (Koordination, ohne Dosierungen)\n"
        "    - ...\n"
    )

    result = _ask_openai_json(
        messages=[
            {"role": "system", "content": sys_part + "\n\n" + json_spec},
            {"role": "user", "content": json.dumps(usr, ensure_ascii=False)},
        ],
        model=MODEL_DEFAULT,
        temperature=0.2,
    )

    beurteilung = (result.get("einschaetzung_text") or "").strip() if isinstance(result, dict) else ""
    prozedere   = (result.get("prozedere_text") or "").strip() if isinstance(result, dict) else ""

    beurteilung = _strip_heading_prefix(beurteilung, "Einschätzung")
    prozedere   = _strip_heading_prefix(prozedere,  "Prozedere")
    beurteilung, prozedere = _rectify_assessment_vs_plan(beurteilung, prozedere)
    prozedere = _ensure_grouped_plan(prozedere)

    if not prozedere:
        fallback = {
            "Setting & Ziele": [
                "Sitzungen 1x/Woche (45–50 min); gemeinsame Zieldefinition (z. B. Schlaf, Aktivierung, Angstreduktion)"
            ],
            "Psychoedukation": [
                "Störungsmodell & Stress-Kreislauf erklären",
                "Umgang mit Grübeln/Vermeidung besprechen"
            ],
            "Aktivitätsaufbau": [
                "3 konkrete, kleine Aktivitäten/Woche planen",
                "Angenehme Aktivitäten und soziale Kontakte dosiert steigern"
            ],
            "Schlaf & Rhythmus": [
                "Konstante Aufstehzeit; Abendroutine",
                "Stimuluskontrolle & Bildschirmreduktion vor dem Schlafen"
            ],
            "Angst/Skills/Exposition": [
                "Atem-/Bodyscan-Übungen täglich 5–10 min",
                "Graduierte Expositionen (hier-und-jetzt, 10–15 min)",
                "Gedankenprotokoll (ABC) + kognitive Umstrukturierung"
            ],
            "Substanzkonsum": [
                "Reduktions-/Abstinenzplan (Alkohol) mit Alternativen",
            ],
            "Sicherheit/Krisenplan": [
                "Krisen-/Sicherheitsplan schriftlich (Frühwarnzeichen, Coping-Schritte)",
                "Notfallkontakte hinterlegen; Erreichbarkeit klären"
            ],
            "Einbezug/Koordination": [
                "Einbezug Angehöriger nach Einwilligung (Rollen klären)",
                "Koordination Hausarzt/Psychiatrie (somatische/med. Mitbeurteilung)"
            ],
            "Diagnostik/Screenings": [
                "Baseline-Skalen (PHQ-9/GAD-7), Re-Test in 2–4 Wochen"
            ],
            "Verlauf/Monitoring": [
                "Schlaf-/Aktivitäts-/Gefühlstagebuch führen",
                "Nächster Termin in 7 Tagen; bei Verschlechterung frühere Wiedervorstellung"
            ],
            "Medikation (Koordination)": [
                "Medikation nur in ärztlicher Koordination, keine Dosierungen dokumentieren"
            ],
        }
        lines = []
        for sec in SECTION_ORDER:
            items = fallback.get(sec, [])
            if items:
                lines.append(f"- {sec}")
                for it in items:
                    lines.append(f"  - {it}")
        prozedere = "\n".join(lines).strip()

    return beurteilung, prozedere


__all__ = [
    "resolve_red_flags_path",
    "generate_full_entries_german",
    "generate_status_gaptext_german",
    "generate_assessment_and_plan_german",
    "compose_erstbericht",
    "reset_openai_client",
    "explain_plan_brief",
]
