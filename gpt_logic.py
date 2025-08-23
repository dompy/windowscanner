# gpt_logic.py
"""
Psychologie-fokussierte Logik für den Praxis-Assistenten
- Saubere Trennung Logik/UI (keine tkinter-Referenzen)
- Zentraler Resolver für Red-Flag-Dateien (Psychologie bevorzugt)
- Schweizer Orthografie, AUSFÜHRLICHER Erstbericht (keine Telegraphie)
- Funktionen, die vom UI genutzt werden:
    * resolve_red_flags_path()
    * generate_full_entries_german()
    * generate_anamnese_gaptext_german()
    * generate_status_gaptext_german()
    * generate_assessment_and_plan_german()
    * compose_erstbericht()
    * format_anamnese_fliess_text()  # optionaler Fallback
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
    "Aktuelle Anamnese (inkl. Suizidalität, falls erwähnt)",
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
    """
    Liefert ein Regex-Alternativenmuster für die Top-Level-Überschriften inkl. geläufiger Varianten
    (Umlaute/ae, &/und, alternative Bezeichnungen).
    """
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
    """Bevorzugt psychologische Red-Flags, fällt ansonsten auf medizinische zurück."""
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
    """
    Zieht fehlplatzierte Einschätzungs-Sätze aus dem Prozedere zurück.
    Heuristik:
    - Im Prozedere bleiben Bullet-/Listenzeilen oder Zeilen, die mit typischen Plan-Köpfen beginnen.
    - Zeilen mit Schlüsselwörtern der Einschätzung (Hypothese, Alternativhypothese, Dringlichkeit, Schweregrad, DD etc.)
      wandern zurück in die Einschätzung.
    - Nackte Freitext-Zeilen ohne Bullet werden standardmässig zur Einschätzung verschoben.
    """
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
            kept.append(raw)
            continue

        is_bullet = bool(re.match(r"^\s*[-•–]\s+", ln))
        starts_like_plan = ln.lower().startswith(plan_heads)

        if is_bullet or starts_like_plan:
            kept.append(raw)
            continue

        if assess_kw.search(ln):
            moved.append(ln)
            continue

        # Freitext ohne Bullet -> tendenziell Einschätzung
        moved.append(ln)

    new_einsch = (einsch.strip() + ("\n\n" + "\n".join(moved).strip() if moved else "")).strip()
    new_proz   = "\n".join(kept).strip()
    return new_einsch, new_proz


def _split_snippets(text: str) -> list[str]:
    """Zerschneidet freien Text in kurze Snippets (Zeilen/Sätze)."""
    if not text:
        return []
    s = text.replace("\r\n", "\n")
    # harte Splits an Zeile/Strichpunkt
    chunks = re.split(r"[;\n]+", s)
    out = []
    for ch in chunks:
        ch = ch.strip(" -•–\t")
        if not ch:
            continue
        # Grob an Satzende splitten (Punkt/!/?), aber kurze Reststücke zusammenlassen
        parts = re.split(r"(?<=[.!?])\s+", ch)
        for p in parts:
            p = p.strip()
            if p:
                out.append(p)
    return out

def _route_snippets_to_sections(text: str) -> Dict[str, list[str]]:
    """
    Ordnet Snippets heuristisch zu:
    - Eintrittssituation: jetzt/heute/vorstellung/erstkonsultation/notfall/überweisung
    - Aktuelle Anamnese: symptome, suizid, auslöser, belastung, substanz, zeitliche Nähe
    - Hintergrund: früher, seit kindheit, vorgeschichte, frühere behandlungen/diagnosen
    - Soziale: arbeit, job, schule, beziehung, partner, freunde, wohnen, unterstützung, netz
    - Familiäre: mutter, vater, schwester, bruder, familie, familiär, erkrankungen in familie
    """
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

        # Default: Aktuelle Anamnese
        buckets["Aktuelle Anamnese"].append(s)

    # Deduplizieren und kurz glätten
    for k in buckets:
        seen = set()
        uniq = []
        for x in buckets[k]:
            if x not in seen:
                seen.add(x); uniq.append(x)
        buckets[k] = uniq
    return buckets

def _strip_question_lines(text: str) -> str:
    """
    Entfernt Fragezeilen aus Freitext:
    - Bullet-Fragen ('- ...?')
    - alleinstehende Fragezeilen ('...?')
    Repariert auch '?n' -> '?\n'.
    """
    if not text:
        return ""
    s = text.replace("\r\n", "\n")
    s = re.sub(r"\?n(\s|$)", "?\n", s)  # Artefakt
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
    # Zeilen, die nur aus einer Überschrift bestehen, entfernen
    s = re.sub(rf"(?im)^\s*(?:{heads})\s*:?\s*$", "", s)
    # Überschrift mit folgendem Doppelpunkt am Zeilenanfang entfernen
    s = re.sub(rf"(?im)^\s*(?:{heads})\s*:\s*", "", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _strip_heading_prefix(block: str, heading: str) -> str:
    """Entfernt eine evtl. mitgelieferte Überschrift ('Einschätzung'/'Prozedere') am Blockanfang."""
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
        "Aktuelle Anamnese (inkl. Suizidalität, falls erwähnt)\n" + join_or_none(b.get("Aktuelle Anamnese", [])),
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
        "Klare Gliederung: Eintrittssituation; Aktuelle Anamnese (inkl. Suizidalität); Hintergrundanamnese; "
        "Soziale Anamnese; Familiäre Anamnese; Psychostatus (heute); Suizidalität inkl. glaubhafter Erklärung & Risikoeinschätzung; "
        "Differenzierte Einschätzung (Distanzierungs-/Absprache-/Bündnisfähigkeit integrieren); Prozedere. "
        "Wörtliche Patienten-Zitate sparsam einstreuen und mit «…» kennzeichnen. "
        "Keine Aufzählungslisten, ausser im Abschnitt «Prozedere»."
    )
    if humanize:
        base += " Ton: menschlich, respektvoll, aber fachlich."
    return base

def _fallback_format_anamnese_local(freetext: str, gaptext: str) -> str:
    """Sehr einfacher Fallback ohne LLM: Bullet-Zeilen glätten (vollständige Sätze)."""
    import re
    parts = []
    ft = (freetext or "").strip()
    if ft:
        if not ft.endswith((".", "!", "?")):
            ft = ft.rstrip() + "."
        parts.append(ft)
    for raw in (gaptext or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        line = re.sub(r"^\s*[–-]\s*", "", line)
        line = re.sub(r"\?\s*ja\s*$", " (bejaht).", line)
        line = re.sub(r"\?\s*nein\s*$", " (verneint).", line)
        if not line.endswith((".", "!", "?")):
            line += "."
        parts.append(line)
    return " ".join(parts).strip()

def format_anamnese_fliess_text(freetext: str, gaptext: str) -> str:
    """
    Formatiert Freitext + 'Erweiterte Anamnese' zu EINEM Fliesstext-Absatz (ausführlicher Stil).
    """
    try:
        sys_msg = (
            "Du bist erfahrener Psychiater/Psychologe in einer Schweizer Praxis. "
            "Formuliere aus a) kurzer freier Anamnese und b) einer Liste mit Zusatzfragen/-antworten "
            "EINEN gut lesbaren Absatz in vollständigen Sätzen (kein Stichwortstil). "
            "Schweizer Orthografie (ss). Keine Diagnosen. Wenn passend, einzelne kurze Zitate des Patienten («…»)."
        ).strip()
        usr = {
            "freitext": freetext or "",
            "zusatzfragen_liste": gaptext or "",
            "hinweis": "Nur EIN Absatz. Keine Bulletpoints."
        }
        text = ask_openai(sys_msg + "\n\nDaten:\n" + json.dumps(usr, ensure_ascii=False))
        text = (text or "").strip()
        if text:
            return " ".join(x.strip() for x in text.splitlines() if x.strip())
    except Exception:
        pass
    return _fallback_format_anamnese_local(freetext, gaptext)

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
    return _normalize_headings_to_spaces(txt, spaces=3)

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
    return _normalize_headings_to_spaces(report, spaces=3)


def _normalize_headings_to_spaces(text: str, spaces: int = 3) -> str:
    if not text:
        return ""
    S = " " * max(1, spaces)
    heads_alt = _top_heads_regex()

    lines = text.replace("\r\n", "\n").split("\n")
    out = []
    i = 0
    while i < len(lines):
        ln = lines[i]

        m_inline = re.match(fr"^\s*(?P<h>{heads_alt})\s*:\s*(?P<rest>.+)$", ln, flags=re.IGNORECASE)
        if m_inline:
            out.append(f"{m_inline.group('h')}{S}{m_inline.group('rest').strip()}")
            i += 1
            continue

        m_head = re.match(fr"^\s*(?P<h>{heads_alt})\s*:?\s*$", ln, flags=re.IGNORECASE)
        if m_head:
            head = m_head.group("h")
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines):
                next_ln = lines[j]
                if not re.match(fr"^\s*(?:{heads_alt})\s*:?\s*$", next_ln, flags=re.IGNORECASE):
                    out.append(f"{head}{S}{next_ln.strip()}")
                    i = j + 1
                    continue
            out.append(head)
            i += 1
            continue

        out.append(ln)
        i += 1

    txt = "\n".join(out)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    return txt

# ------------------ 4 Felder – Psychologie ------------------

def _enforce_psych_erstgespraech_layout(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Erzwingt saubere Abschnitte (ohne Doppelpunkt-Überschriften) für die Anamnese
    und ergänzt bei Bedarf den Risiko-Block im Status.
    Format:
    <Überschrift>\n<Inhalt>\n\n<Überschrift>\n<Inhalt> ...
    """
    import re

    # Kanonische Reihenfolge + tolerantes Matching
    CANON = [
        ("Eintrittssituation", r"eintrittssituation"),
        ("Aktuelle Anamnese (inkl. Suizidalität, falls erwähnt)", r"aktuelle\s+anamnese(?:\s*\(.*?\))?"),
        ("Hintergrundanamnese", r"hintergrund\s*anamnese|hintergrundanamnese"),
        ("Soziale Anamnese", r"soziale\s+anamnese"),
        ("Familiäre Anamnese", r"famili(?:ä|a)re\s+anamnese|familienanamnese"),
    ]

    def _split_by_heads(text: str) -> Dict[str, str]:
        text = (text or "").strip()
        if not text:
            return {}
        # Header am Zeilenanfang, optional mit Doppelpunkt am Ende
        head_pat = r"(?im)^(?P<h>(?:{alts}))\s*:?\s*$".format(
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
            # Eventuell „hängengebliebene“ Head-Zeilen im Inhalt entfernen
            raw = re.sub(head_pat, "", raw).strip()
            raw = re.sub(r"\n{3,}", "\n\n", raw).strip()
            label = _canon_label(m.group("h"))
            if label not in parts or (len(raw) > len(parts[label])):
                parts[label] = raw
        return parts

    # --- Anamnese neu aufbauen (ohne Doppelpunkte) ---
    anamnese_in = payload.get("anamnese_text", "") or ""
    # Leading-Freitext vor erster Überschrift auffangen
    lead_txt = ""
    m_first = re.search(r"(?im)^(eintrittssituation|aktuelle\s+anamnese(?:\s*\(.*?\))?|hintergrund\s*anamnese|hintergrundanamnese|soziale\s+anamnese|famili(?:ä|a)re\s+anamnese|familienanamnese)\s*:?\s*$", anamnese_in)
    if m_first and m_first.start() > 0:
        lead_txt = _strip_question_lines(anamnese_in[:m_first.start()].strip())

    found = _split_by_heads(anamnese_in)
    rebuilt: List[str] = []
    for label, _ in CANON:
        content = (found.get(label) or "").strip()
        content = re.sub(
            r"(?im)^(eintrittssituation|aktuelle\s+anamnese(?:\s*\(.*?\))?|hintergrund\s*anamnese|hintergrundanamnese|soziale\s+anamnese|famili(?:ä|a)re\s+anamnese|familienanamnese)\s*:?\s*$",
            "", content
        ).strip()

        # Leading-Freitext sinnvoll verteilen
        if lead_txt:
            if label.startswith("Eintrittssituation") and not content:
                content = lead_txt; lead_txt = ""
            elif label.startswith("Aktuelle Anamnese") and not content:
                content = lead_txt; lead_txt = ""

        if not content:
            content = "keine Angaben"
        rebuilt.append(f"{label}\n{content}")

    payload["anamnese_text"] = "\n\n".join(rebuilt).strip()

    # --- Status sichern ---
    status = (payload.get("status_text") or "").strip()
    if status and not status.lower().startswith("psychostatus"):
        status = f"Psychostatus (heute)\n{status}".strip()

    # Schritt 4: robustes Risiko-Heading + Umlautvarianten
    risk_pat = r"(?im)^\s*Suizidalität\s*(?:&|und)\s*Risikoeinsch(?:ä|ae)tzung\s*$"
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
    """
    Erzeugt vier dokumentationsfertige Felder (Anamnese/Psychostatus/Einschätzung/Prozedere)
    im AUSFÜHRLICHEN Erstbericht-Stil (Absätze, vollständige Sätze).
    """
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
        "  2) Aktuelle Anamnese (inkl. Suizidalität, falls erwähnt)\n"
        "  3) Hintergrundanamnese \n"
        "  4) Soziale Anamnese \n"
        "  5) Familiäre Anamnese \n"
        "- Status: **Psychostatus (heute)** als Absätze: Erscheinung/Verhalten; Sprache/Denken; Stimmung/Affekt; Wahrnehmung; Kognition/Orientierung.\n"
        "- Risiko: Abschnitt «Suizidalität & Risikoeinschätzung» explizit ausweisen (ja/nein/unklar) inkl. glaubhafter Erklärung zu **Distanzierungsfähigkeit**, **Absprachefähigkeit**, **Bündnisfähigkeit** kurz einordnen.\n"
        "- Einschätzung: differenzierte, ausführliche, psychologische Fallformulierung (Auslöser/aufrechterhaltende Faktoren/Ressourcen, Funktionsniveau) + Schweregrad (leicht/mittel/schwer) + Dringlichkeit (niedrig/mittel/hoch). 2–4 Alternativhypothesen, falls sinnvoll.\n"
        "- Prozedere: klare Bulletpoints (Interventionen, Sicherheit/Krisenplan, Einbezug Dritter nach Einwilligung, Verlauf/Kontrolle, Koordination; Medikation nur allgemein, ohne Dosierungen).\n"
        "- Antworte **ausschliesslich** als JSON:\n"
        "{\n"
        "  \"anamnese_text\": \"Eintrittssituation\\n...\\n\\nAktuelle Anamnese (inkl. Suizidalität, falls erwähnt)\\n...\\n\\nHintergrundanamnese\\n...\\n\\nSoziale Anamnese\\n...\\n\\nFamiliäre Anamnese\\n...\",\n"
        "  \"status_text\": \"Psychostatus (heute)\\nErscheinung/Verhalten ...\\nSprache/Denken ...\\nStimmung/Affekt ...\\nWahrnehmung ...\\nKognition/Orientierung ...\\n\\nSuizidalität & Risikoeinschätzung\\nDistanzierungsfähigkeit: ja/nein/unklar | Absprachefähigkeit: ja/nein/unklar | Bündnisfähigkeit: ja/nein/unklar\",\n"
        "  \"beurteilung_text\": \"...\",\n"
        "  \"prozedere_text\": \"- ...\\n- ...\"\n"
        "}\n"
    ).strip()

    # Vorverarbeitung
    clean_input = _strip_top_level_headings(_strip_question_lines(user_input))
    usr_payload = {"eingabetext": clean_input, "kontext": context}

    result = _ask_openai_json(
        messages=[{"role": "system", "content": sys_msg},
                  {"role": "user", "content": json.dumps(usr_payload, ensure_ascii=False)}]
    )

    if isinstance(result, dict):
        if result.get("anamnese_text"):
            result["anamnese_text"] = _strip_question_lines(result["anamnese_text"])
        result = _enforce_psych_erstgespraech_layout(result)
        ana_txt = (result.get("anamnese_text") or "")
        if not re.search(r"(?im)^(Eintrittssituation|Aktuelle Anamnese|Hintergrundanamnese|Soziale Anamnese|Familiäre Anamnese)\b", ana_txt):
            result["anamnese_text"] = _fallback_rebuild_anamnese(clean_input)
        if red_flags_list:
            result["red_flags"] = red_flags_list

    full_block = _format_full_entries_block(result if isinstance(result, dict) else {})
    return result, full_block

# ------------------ Anamnese → Zusatzfragen ------------------

def generate_anamnese_gaptext_german(
    anamnese_raw: str,
    answered_context: Optional[str] = "",
    humanize: bool = True,
) -> Tuple[Dict[str, Any], str]:
    """Erzeugt 2–5 gezielte, psychologisch relevante Zusatzfragen (kurz & patientenverständlich)."""

    def _sys_msg_base() -> str:
        return (
            "Du bist erfahrener Psychologe in einer Schweizer Praxis.\n"
            "Aufgabe: Analysiere den Freitext und formuliere **2–5 gezielte Zusatzfragen**, "
            "um Anliegen, Schweregrad und Dringlichkeit einzugrenzen.\n"
            "Fokus: Beginn/Verlauf; Auslöser/Belastung; Ressourcen/Schutzfaktoren; Funktionsniveau (Arbeit/Beziehung/Alltag); "
            "Substanzkonsum; bisherige Behandlungen/Hilfen; **Risiko** (Suizidalität/Fremdgefährdung: ja/nein/unklar, Schutzfaktoren).\n"
            "WICHTIG: keine Diagnosen, keine Testlisten — nur kurze, patientenverständliche Fragen.\n"
            "Fasse Eingaben zusammen (paraphrasieren), transformiere Stichworte/Fragment-Sätze in vollständige Sätze. **Fragezeilen NICHT übernehmen**, sondern nur die inhaltlichen Antworten.\n"
            "Antworte ausschliesslich als JSON:\n{\n  \"zusatzfragen\": [\"Frage 1\", \"Frage 2\"]\n}\n"
        ).strip()

    sys_msg = _sys_msg_base()

    usr = {
        "eingabe_freitext": anamnese_raw,
        "bereits_beantwortet": answered_context or "",
        "hinweise": "Priorisiere Risikoabschätzung und nächste sinnvolle Klärungsschritte.",
    }

    result = _ask_openai_json(
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": json.dumps(usr, ensure_ascii=False)},
        ]
    )

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
            "Gibt es aktuell Suizidgedanken oder Gedanken, jemandem zu schaden?",
            "Welche Situationen oder Gedanken verschlimmern bzw. bessern die Symptome?",
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

# ------------------ Psychopathologischer Befund (Lückentext) ------------------

def generate_status_gaptext_german(
    anamnese_filled: str,
    humanize: bool = True,
    phase: str = "initial",
) -> Tuple[Dict[str, Any], str]:
    """Liefert psychologischen Status/Exploration als Lückentext/Checkliste (zum Ausfüllen)."""
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
    """Erzeugt 'Einschätzung' (Hypothesen + Schweregrad/Dringlichkeit) und 'Prozedere' (Bulletpoints)."""
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

    # --- JSON-basiert statt Fliesstext-Split ---
    json_spec = (
        "Antworte ausschliesslich als JSON-Objekt mit genau diesen Schlüsseln:\n"
        "{\n"
        "  \"einschaetzung_text\": \"string\",\n"
        "  \"prozedere_text\": \"string\"\n"
        "}\n"
        "- Strikte Trennung: Begründungen/Hypothesen/Dringlichkeit NUR in \"einschaetzung_text\".\n"
        "- In \"prozedere_text\" NUR umsetzbare Schritte, als Bulletpoints (je Zeile mit '-' beginnen), keine Begründungen.\n"
        "- Umfang: 10–16 Bullets, thematisch breit: Setting/Ziele; Psychoedukation; Aktivitätsaufbau; Schlaf/Regelmässigkeit; Angst-/Exposition/Skills; Substanzkonsum (reduktions-/Abstinenzplan, falls relevant); Krisen-/Sicherheitsplan & Notfallkontakte; Einbezug Dritter (nach Einwilligung); Koordination (Hausarzt/Psychiatrie); Diagnostische Vertiefung/Screenings (indikationsbezogen); Verlauf/Monitoring (Skalen, Hausaufgaben, Termine); Arbeits-/Sozialthemen; Medikation nur als Koordination (ohne Dosierungen).\n"
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

    # Headings entfernen + Heuristik: rationale Sätze aus Prozedere zurück in Einschätzung
    beurteilung = _strip_heading_prefix(beurteilung, "Einschätzung")
    prozedere   = _strip_heading_prefix(prozedere,  "Prozedere")
    beurteilung, prozedere = _rectify_assessment_vs_plan(beurteilung, prozedere)

    # Minimal-Fallback, falls das Modell kein Prozedere liefert
    if not prozedere:
        prozedere = "\n".join([
            "- Setting: wöchentliche Sitzungen, Zielvereinbarung (Schlaf, Aktivierung, Angstreduktion)",
            "- Psychoedukation zu Stress/Angst/Depression, Modell erklären",
            "- Aktivitätsaufbau (3 kleine, konkrete Aktivitäten/Woche, Plan schriftlich)",
            "- Schlafhygiene & Rhythmus (Aufstehzeit fix, Abendroutine, Bildschirmreduktion)",
            "- Angstbewältigung: Atem-/Bodyscan, kurze Expositionen (hier & jetzt, 10–15 min)",
            "- Gedankenprotokoll (ABC), kognitive Umstrukturierung in der Sitzung",
            "- Substanz: Bierreduktion/Abstinenzplan, Alternativen definieren",
            "- Krisen-/Sicherheitsplan schriftlich; Notfallkontakte; Frühwarnzeichen",
            "- Einbezug Schwester/Nichte nach Einwilligung (Unterstützungsrolle klären)",
            "- Koordination Hausarzt (somatische Abklärung, medikamentöse Mitbeurteilung)",
            "- Screenings (PHQ-9/GAD-7) baseline, in 2–4 Wochen wiederholen",
            "- Hausaufgabe: Schlaf-/Aktivitäts-/Gefühlstagebuch (täglich kurz)",
            "- Verlauf: Termin in 7 Tagen; bei Verschlechterung frühere Wiedervorstellung",
        ])

    return beurteilung, prozedere



__all__ = [
    "resolve_red_flags_path",
    "generate_full_entries_german",
    "generate_anamnese_gaptext_german",
    "generate_status_gaptext_german",
    "generate_assessment_and_plan_german",
    "compose_erstbericht",
    "format_anamnese_fliess_text",
    "reset_openai_client",
]
