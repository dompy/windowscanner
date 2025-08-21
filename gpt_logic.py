# gpt_logic.py  — CLEAN & SWISS-STYLE
 
# gestern abend schüttelfrost, dann gliederschmerzen, fieber 39°, verwirrt gewesen, auf dafalgan fieber regredient.
# seit 3 tagen husten mit gelb-grünem auswurf, atemnot beim treppensteigen, letzte nacht leicht fieber, kein brustschmerz.
# seit heute morgen starke schmerzen im rechten unterbauch, übelkeit, kein erbrechen, kein durchfall, kein fieber gemessen.
# vor einer woche umgeknickt, seitdem schwellung und schmerz am rechten sprunggelenk, belastung kaum möglich, keine offene wunde.
# seit 2 wochen müde, blass, appetitlos, in letzter zeit häufig schwindel beim aufstehen, keine magen-darm-beschwerden.
# seit gestern juckender ausschlag an beiden armen, nach gartenarbeit aufgetreten, keine atemnot, kein fieber.

import os
import re
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

_LAB_KWS = ["labor", "blutbild", "bb", "crp", "leuko", "lc", "elektrolyt", "natrium", "kalium",
            "bz", "blutzucker", "kreatinin", "gfr", "leberwerte", "urin", "urin-stix", "stix"]
_IMG_KWS = ["ultraschall", "sono", "us ", " ct", "ct ", "mrt", "rx", "röntgen", "roentgen", "bildgebung"]

def _contains_any(s: str, kws: list[str]) -> bool:
    s = s.lower()
    return any(k in s for k in kws)

def _dedupe_diagnostics_in_plan(befunde_text: str, plan_text: str) -> str:
    """Entfernt Labor/Bildgebung aus dem Plan, wenn diese bereits in 'Befunde' stehen.
       Bildgebung/Labor bleibt nur in der '- Bei Persistenz/Progredienz:'-Zeile erlaubt."""
    if not plan_text:
        return plan_text or ""
    bef = (befunde_text or "").lower()
    labs_in_bef = _contains_any(bef, _LAB_KWS)
    img_in_bef  = _contains_any(bef, _IMG_KWS)

    lines = re.split(r"\r?\n", plan_text)
    kept: list[str] = []
    for ln in lines:
        raw = ln.strip()
        lower = raw.lower().lstrip("-• ").strip()
        if lower.startswith("bei persist"):
            kept.append(ln)  # immer behalten
            continue
        if labs_in_bef and _contains_any(lower, _LAB_KWS):
            continue
        if img_in_bef and _contains_any(lower, _IMG_KWS):
            continue
        kept.append(ln)
    return "\n".join(kept).strip()

def _ensure_persistence_line(plan_text: str, default_hint: str = "") -> str:
    """Sichert, dass genau eine Persistenz-Zeile vorhanden ist; falls nicht, fügt eine generische an."""
    if not plan_text:
        plan_text = ""
    if re.search(r"(?im)^\s*[-•]?\s*bei\s+persist", plan_text):
        return plan_text.strip()
    hint = default_hint or "Bei Persistenz/Progredienz: weiterführende Diagnostik (Bildgebung oder erweitertes Labor) erwägen."
    sep = "\n" if not plan_text.endswith("\n") else ""
    return (plan_text.strip() + sep + hint).strip()

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

def _normalize_plan_bullets(text: str) -> str:
    """
    Vereinheitlicht den Plan:
    - trennt Inline-Bullets (•/–/—/-) in eigene Zeilen
    - entfernt führende Bullet-Zeichen / Whitespaces je Zeile
    - setzt pro Zeile genau ein "- "
    """
    if not text:
        return "- keine Angaben"

    t = text.strip()

    # 1) Inline-Separators in Zeilenumbrüche umwandeln
    #    " • " / "•" / " – " / " — " / " - "  →  "\n"
    t = re.sub(r"[ \t]*[•\u2022][ \t]*", "\n", t)
    t = re.sub(r"\s[–—-]\s+", "\n", t)  # Gedankenstrich/Minus zwischen Wörtern

    # 2) Auf Zeilen aufteilen, leere entfernen
    lines = [ln.strip() for ln in t.splitlines()]
    lines = [ln for ln in lines if ln]

    # 3) Führende Bulletzeichen je Zeile entfernen und normieren
    norm: list[str] = []
    for ln in lines:
        # Leitet gelegentlich mit Bullet/Listenzeichen ein
        ln = re.sub(r"^[\-\u2022•\*\u2013\u2014]+\s*", "", ln)
        if not ln:
            continue
        norm.append(f"- {ln}")

    return "\n".join(norm) if norm else "- keine Angaben"

def _parse_ottawa_from_text(befunde_text: str) -> dict:
    """
    Grobheuristik für Ottawa‑Ankle‑Rules anhand frei editierten Befundtextes.
    Wir suchen nach Hinweisen auf:
      - Knochen‑Druckschmerz Malleolus (lat/med)
      - Basis MT5
      - Os naviculare
      - 4 Schritte möglich / nicht möglich
      - Rx durchgeführt / Fraktur festgestellt
    """
    t = (befunde_text or "").lower()

    def has_any(*words: str) -> bool:
        return any(w in t for w in words)

    malleolus_pain = has_any("druckdolenz malleolus", "schmerz malleolus", "malleolus lat", "malleolus med")
    mt5_pain      = has_any("basis os metatarsale v", "basis mt5", "mt5 schmerz", "druckdolenz mt5")
    nav_pain      = has_any("os naviculare", "naviculare schmerz", "naviculare druckdolenz")
    no_4_steps    = has_any("4 schritte nicht", "keine 4 schritte", "gehunfähig", "nicht belastbar", "keine belastung")
    can_4_steps   = has_any("4 schritte möglich", "belastbar", "gehen möglich")

    rx_done       = has_any("rx", "röntgen", "roentgen")
    fracture      = has_any("fraktur", "frakturlinie", "bruch")
    no_fracture   = has_any("keine fraktur", "ohne fraktur", "fraktur ausgeschlossen")

    # Ottawa positiv, wenn (Knochendruckschmerz an den 3 Arealen) ODER Unfähigkeit zu 4 Schritten
    ottawa_positive = (malleolus_pain or mt5_pain or nav_pain) or no_4_steps
    ottawa_negative = can_4_steps and not (malleolus_pain or mt5_pain or nav_pain)

    return {
        "ottawa_positive": bool(ottawa_positive) and not ottawa_negative,
        "ottawa_negative": bool(ottawa_negative),
        "rx_done": bool(rx_done),
        "fracture": bool(fracture) and not no_fracture,
        "no_fracture": bool(no_fracture),
    }

def _guess_focus_simple(text: str) -> str:
    """
    Sehr schlanke Leitsymptom-Heuristik (nur lokal in gpt_logic.py),
    damit wir keine Abhängigkeit zur UI-Datei haben.
    """
    t = (text or "").lower()

    # Muskuloskelettal
    if any(k in t for k in ("sprunggelenk", "osg", "fuss", "fuß", "fussgelenk", "fußgelenk", "umknick", "umgeknickt", "achill")):
        return "msk_ankle"
    if any(k in t for k in ("hand", "handgelenk", "wrist", "karpal", "karpaltunnel", "skaphoid", "tabatiere")):
        return "msk_wrist"

    # Wirbelsäule / Rücken
    if any(k in t for k in ("rücken", "ruecken", "lumb", "lws", "ischias")):
        return "lws"

    # Innere
    if any(k in t for k in ("brust", "thorax", "atemnot", "brustschmerz")):
        return "thorax"
    if any(k in t for k in ("bauch", "abdomen", "übelkeit", "erbrechen", "durchfall")):
        return "abdomen"
    if any(k in t for k in ("hals", "husten", "halsweh", "halsschmerz", "schnupfen")):
        return "hno"
    if any(k in t for k in ("kopfschmerz", "schwindel", "synkope")):
        return "neuro"

    return "allg"


def _guess_focus_from_all(anamnese_text: str, befunde_text: str) -> str:
    """
    Re-Use: kombiniere Anamnese + Befunde als Signal und wähle Fokus.
    """
    txt = (anamnese_text or "") + " " + (befunde_text or "")
    # harte Keys zuerst (überschreiben einfache Matches)
    t = txt.lower()
    if any(k in t for k in ("sprunggelenk", "osg", "fuss", "fuß", "umknick", "umgeknickt", "achill")):
        return "msk_ankle"
    if any(k in t for k in ("hand", "handgelenk", "wrist", "karpal", "skaphoid", "tabatiere")):
        return "msk_wrist"
    # sonst einfache Heuristik
    return _guess_focus_simple(txt)


def pre_assessment_gate(anamnese_text: str, befunde_text: str) -> Optional[dict]:
    """
    Prüft scorebasierte Gates (derzeit: Ottawa Ankle).
    Falls Ottawa positiv und kein Rx‑Resultat vorhanden → blockiert finale LLM‑Beurteilung
    und liefert einen Interims‑Plan.
    Rückgabe None = kein Block; sonst Dict mit 'assessment' und 'plan'.
    """
    focus = _guess_focus_from_all(anamnese_text, befunde_text)
    if focus != "msk_ankle":
        return None

    o = _parse_ottawa_from_text(befunde_text)
    if not o.get("ottawa_positive"):
        return None  # kein Block nötig

    # Wenn positiv aber Rx/Befund fehlt → Interimsplan ausgeben
    if not o.get("rx_done") or (not o.get("fracture") and not o.get("no_fracture")):
        assessment = "OSG‑Distorsion (Verdacht). Ottawa‑Ankle‑Rules positiv – Fraktur bis Rx‑Befund nicht ausgeschlossen."
        plan = "\n".join([
            "- Schonung, Kühlung, Hochlagern, Kompression (RICE).",
            "- Analgetika bei Bedarf (allgemein).",
            "- Röntgen OSG/Fuss gemäss Ottawa‑Regeln veranlasst.",
            "- Verlaufskontrolle in 24–48 h oder nach Vorliegen Rx‑Befund.",
            "- Vorzeitige Wiedervorstellung bei zunehmendem Schmerz/Schwellung, Gefühlsstörungen oder Durchblutungsstörung.",
            "Bei Persistenz/Progredienz: weiterführende Bildgebung (US/MRI) oder Ortho‑Beurteilung erwägen."
        ])
        return {"block": True, "assessment": assessment, "plan": plan}

    # Wenn Rx bereits vorhanden → kurze Bewertung je nach Frakturstatus vorschlagen
    if o.get("fracture"):
        assessment = "OSG‑Trauma mit nachgewiesener Fraktur (Rx)."
        plan = "\n".join([
            "- Immobilisation/Entlastung (z. B. Vacoped/Schiene) – nach lokaler Praxis.",
            "- Analgetika (allgemein).",
            "- Ortho/Unfallchirurgie zur weiteren Behandlung.",
            "- Verlaufskontrolle/Termin gemäss Fachdisziplin.",
            "Bei Persistenz/Progredienz: zusätzliche Bildgebung/MRI nach Fachentscheid."
        ])
        return {"block": True, "assessment": assessment, "plan": plan}

    if o.get("no_fracture"):
        # Ottawa positiv, Rx ohne Fraktur → Distorsionsmanagement
        assessment = "OSG‑Distorsion ohne Fraktur (Rx)."
        plan = "\n".join([
            "- RICE: Schonung, Kühlung, Kompression, Hochlagern.",
            "- Frühfunktionelle Mobilisation/Physio nach Schmerz.",
            "- Analgetika bei Bedarf (allgemein).",
            "- Verlaufskontrolle in 5–7 Tagen.",
            "- Vorzeitige Wiedervorstellung bei Zunahme der Beschwerden oder Neurologiezeichen.",
            "Bei Persistenz/Progredienz: US/MRI; Ortho/Physio erwägen."
        ])
        return {"block": True, "assessment": assessment, "plan": plan}

    return None

def discover_relevant_scores(anamnese_raw: str, befunde_text: str = "", model: str = MODEL_DEFAULT) -> Dict[str, Any]:
    """
    Lässt das LLM max. 2 klinische Scores/Decision-Tools vorschlagen,
    die zur aktuellen Anamnese/Befunde passen (Hausarzt, Schweiz/Europa).
    Rückgabeformat ist strikt und UI-freundlich.

    JSON-Schema:
    {
      "scores": [
        {
          "name": "Ottawa Ankle Rules",
          "version": "canonical/aktuell",
          "applicable": true,
          "why": "kurze Begründung",
          "items": [
            {"key":"malleolus_tenderness","label":"Knochendruckschmerz Malleolus (lat/med)","collect_in":"befunde"},
            {"key":"mt5_tenderness","label":"Knochendruckschmerz Basis MT5","collect_in":"befunde"},
            {"key":"navicular_tenderness","label":"Knochendruckschmerz Os naviculare","collect_in":"befunde"},
            {"key":"unable_4_steps","label":"4 Schritte nicht möglich","collect_in":"befunde"},
            {"key":"xray_done","label":"Röntgen durchgeführt","collect_in":"bildgebung"},
            {"key":"fracture_on_xray","label":"Fraktur im Röntgen","collect_in":"bildgebung"}
          ],
          "gate": {
            "positive_when": "kurze Logik in Worten",
            "action_if_positive": "Röntgen OSG/Fuss",
            "action_if_incomplete": "fehlende Items gezielt erheben",
            "action_if_negative": "kein Rx erforderlich"
          }
        }
      ]
    }
    """
    allowed = [
        "Ottawa Ankle Rules", "Revised Geneva Score", "Wells DVT", "Centor/McIsaac",
        "CURB-65", "PERC", "Ottawa Knee", "Canadian C-Spine", "qSOFA", "CHA2DS2-VASc"
    ]
    sys_msg = (
        "Du bist erfahrener Hausarzt in der Schweiz. Wähle höchstens ZWEI klinische Scores/Decision-Tools, "
        "die für die geschilderte Situation in der Grundversorgung relevant sind. "
        "NUTZE NUR die folgende Whitelist (keine neuen Scores erfinden): "
        + ", ".join(allowed) + ". "
        "Fokussiere auf Werkzeuge, die die nächste diagnostische/therapeutische Entscheidung beeinflussen "
        "(z. B. Bildgebung/POCT). "
        "Ordne JEDE Erhebungs-Variable eindeutig einer Kategorie zu: 'anamnese' | 'befunde' | 'labor' | 'bildgebung'. "
        "Schweizer/Europäische Praxis. Antworte NUR als gültiges JSON nach dem angegebenen Schema."
    )
    ctx = {
        "anamnese": (anamnese_raw or "").strip(),
        "befunde": (befunde_text or "").strip(),
        "schema": "scores[*].{name,version,applicable,why,items[*]{key,label,collect_in},gate{positive_when,action_if_positive,action_if_incomplete,action_if_negative}}"
    }
    try:
        out = _ask_openai_json(
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": json.dumps(ctx, ensure_ascii=False)},
            ],
            model=model, temperature=0.2
        )
    except Exception as e:
        logging.exception("discover_relevant_scores failed: %s", e)
        out = {}
    # Schema absichern
    scores = out.get("scores") if isinstance(out, dict) else None
    if not isinstance(scores, list):
        scores = []
    # nur 2 behalten, Felder hart validieren
    def _clean_item(it: Dict[str, Any]) -> Dict[str, str]:
        return {
            "key": str(it.get("key", "")).strip(),
            "label": str(it.get("label", "")).strip(),
            "collect_in": str(it.get("collect_in", "")).strip().lower(),
        }
    cleaned = []
    for sc in scores[:2]:
        cleaned.append({
            "name": str(sc.get("name","")).strip(),
            "version": str(sc.get("version","")).strip() or "canonical",
            "applicable": bool(sc.get("applicable", True)),
            "why": str(sc.get("why","")).strip(),
            "items": [_clean_item(i) for i in (sc.get("items") or []) if i],
            "gate": {
                "positive_when": str((sc.get("gate") or {}).get("positive_when","")).strip(),
                "action_if_positive": str((sc.get("gate") or {}).get("action_if_positive","")).strip(),
                "action_if_incomplete": str((sc.get("gate") or {}).get("action_if_incomplete","")).strip(),
                "action_if_negative": str((sc.get("gate") or {}).get("action_if_negative","")).strip(),
            }
        })
    return {"scores": cleaned}

# ---------- Score-Auswertung: Ottawa Ankle + Revised Geneva ----------

def _normalize_ottawa_criteria(raw: dict) -> dict:
    """
    Vereinheitlicht diverse Key-Varianten (Englisch/Deutsch/LLM) auf:
      malleolus, mt5, nav, no4steps, rx_done, fracture, no_fracture
    Erkennt sowohl englische als auch deutsche Synonyme.
    """
    raw = {str(k).lower(): bool(v) for k, v in (raw or {}).items()}

    def present(patterns: list[str]) -> bool:
        # True, wenn irgendein gesetzter Key eines der Muster enthält
        for k, v in raw.items():
            if not v:
                continue
            for p in patterns:
                if p in k:
                    return True
        return False

    return {
        # Knochen-Druckschmerz Malleolus (lat/med)
        "malleolus": (
            raw.get("malleolus", False)
            or present(["malleol", "malleolus", "knöchel", "knoechel"])
        ),
        # Basis des 5. Metatarsale
        "mt5": (
            raw.get("mt5", False)
            or present(["mt5", "metatars", "mittelfuss", "mittelfuß", "5.", "fuenf", "fünft"])
        ),
        # Os naviculare (Kahnbein)
        "nav": (
            raw.get("nav", False)
            or present(["navicul", "kahnbein", "os nav"])
        ),
        # 4 Schritte nicht möglich / nicht belastbar
        "no4steps": (
            raw.get("no4steps", False)
            or present([
                "unable_4", "4 step", "4 schritt", "no4",
                "keine 4", "nicht belast", "unfaehig", "unfähig",
                "bear_weight", "weight", "gehen nicht", "gehunf"
            ])
        ),
        # Bildgebung/Resultat
        "rx_done":     raw.get("rx_done", False)     or present(["xray", "röntgen", "roentgen", "rx"]),
        "fracture":    raw.get("fracture", False)    or present(["fracture_on_xray", "fraktur", "bruch"]),
        "no_fracture": raw.get("no_fracture", False) or present(["no_fracture", "keine fraktur", "ohne fraktur", "negativ"]),
    }


def ottawa_recommendation(criteria: dict) -> dict:
    """
    Erwartet beliebige Key-Varianten (Englisch/Deutsch/LLM); wird intern normalisiert.
    Rückgabe: {"block": bool, "assessment": str, "plan": str, "summary": str}
    """
    c = _normalize_ottawa_criteria(criteria or {})  # ⬅️ wichtig!
    positive = any((c["malleolus"], c["mt5"], c["nav"], c["no4steps"]))

    if not positive:
        return {"block": False, "summary": "Ottawa negativ – Rx nicht zwingend."}

    # Rx indiziert, aber (noch) kein valider Befund → Interims-Plan + Block
    if not c["rx_done"] or (not c["fracture"] and not c["no_fracture"]):
        assess = "OSG-Distorsion (Verdacht). Ottawa-Ankle-Rules positiv – Fraktur bis Rx-Befund nicht ausgeschlossen."
        plan = "\n".join([
            "- Schonung, Kühlung, Hochlagern, Kompression (RICE).",
            "- Analgetika bei Bedarf (allgemein).",
            "- Röntgen OSG/Fuss gemäss Ottawa-Regeln veranlasst.",
            "- Verlaufskontrolle in 24–48 h oder nach Vorliegen Rx-Befund.",
            "- Vorzeitige Wiedervorstellung bei zunehmenden Schmerzen/Schwellung, Neurologie- oder Durchblutungsstörung.",
            "Bei Persistenz/Progredienz: weiterführende Bildgebung (US/MRI) oder Ortho-Beurteilung erwägen."
        ])
        return {"block": True, "assessment": assess, "plan": plan,
                "summary": "Ottawa positiv – Rx indiziert (ausstehend)."}

    # Rx vorhanden → je nach Befund
    if c["fracture"]:
        assess = "OSG-Trauma mit nachgewiesener Fraktur (Rx)."
        plan = "\n".join([
            "- Immobilisation/Entlastung (z. B. Schiene/Vacoped).",
            "- Analgetika (allgemein).",
            "- Ortho/Unfallchirurgie zur Weiterbehandlung.",
            "- Verlauf gemäss Fachdisziplin.",
            "Bei Persistenz/Progredienz: zusätzliche Bildgebung nach Fachentscheid."
        ])
        return {"block": True, "assessment": assess, "plan": plan,
                "summary": "Fraktur nachgewiesen – Fachdisziplin."}

    if c["no_fracture"]:
        assess = "OSG-Distorsion ohne Fraktur (Rx)."
        plan = "\n".join([
            "- RICE: Schonung, Kühlung, Kompression, Hochlagern.",
            "- Frühfunktionelle Mobilisation/Physio nach Schmerz.",
            "- Analgetika bei Bedarf (allgemein).",
            "- Verlaufskontrolle in 5–7 Tagen.",
            "- Vorzeitige Wiedervorstellung bei Zunahme der Beschwerden oder Neurologiezeichen.",
            "Bei Persistenz/Progredienz: US/MRI; Ortho/Physio erwägen."
        ])
        return {"block": True, "assessment": assess, "plan": plan,
                "summary": "Ottawa positiv, Rx ohne Fraktur – Distorsionsmanagement."}

    return {"block": False, "summary": "Ottawa positiv – Status unklar (Angaben unvollständig)."}

# Revised Geneva – einfache Punktewertung (Skeleton, CH/Europa üblich)
GENEVA_WEIGHTS = {
    "age_65_plus": 1,
    "prev_dvt_pe": 3,
    "surgery_fracture_1m": 2,
    "active_cancer": 2,
    "unilateral_leg_pain": 3,
    "hemoptysis": 2,
    "hr_75_94": 3,
    "hr_95_plus": 5,
    "pain_on_deep_vein_palp_and_unilateral_edema": 4,
}
GENEVA_CATEGORIES = [
    ("low", 0, 3),
    ("intermediate", 4, 10),
    ("high", 11, 1000),
]

def _geneva_points(criteria: dict) -> tuple[int, str]:
    s = 0
    for k, w in GENEVA_WEIGHTS.items():
        if criteria.get(k):
            s += w
    cat = "low"
    for name, lo, hi in GENEVA_CATEGORIES:
        if lo <= s <= hi:
            cat = name; break
    return s, cat

def geneva_recommendation(criteria: dict) -> dict:
    """
    Erwartet Keys laut GENEVA_WEIGHTS (bool). Rückgabe: {"block": False, "summary": "..."}.
    """
    pts, cat = _geneva_points(criteria or {})
    if cat == "high":
        rec = "Hohe klinische Wahrscheinlichkeit – CTPA (CT Thorax mit KM) erwägen; D-Dimer nicht notwendig."
    elif cat == "intermediate":
        rec = "Intermediäre Wahrscheinlichkeit – D-Dimer testen; bei positivem D-Dimer CTPA."
    else:
        rec = "Niedrige Wahrscheinlichkeit – primär D-Dimer; nur bei positivem Test Bildgebung."
    return {"block": False, "summary": f"Revised Geneva: {pts} Punkte ({cat}). {rec}"}



# ------------------ 4 Felder – fix & fertig ------------------

def _format_full_entries_block(payload: Dict[str, Any]) -> str:
    """Kopierfertiger Block mit allen vier Feldern (Red Flags separat im UI)."""

    def _normalize_bullets(text: str) -> str:
        lines = (text or "").splitlines()
        norm = []
        for ln in lines:
            s = ln.strip()
            if not s:
                continue
            # vorhandene Bulletzeichen entfernen und genau ein "- " setzen
            s = s.lstrip("-• ").strip()
            norm.append(f"- {s}")
        return "\n".join(norm) if norm else "- keine Angaben"

    ana = (payload.get("anamnese_text") or "keine Angaben").strip()
    bef = (payload.get("befunde_text") or "keine Angaben").strip()
    beu = (payload.get("beurteilung_text") or "keine Angaben").strip()
    proz_raw = (payload.get("prozedere_text") or "keine Angaben").strip()
    proz = _normalize_bullets(proz_raw)

    parts: list[str] = []
    parts.append("Anamnese:")
    parts.append(ana)
    parts.append("")
    parts.append("Befunde:")
    parts.append(bef)
    parts.append("")
    parts.append("Beurteilung:")
    parts.append(beu)
    parts.append("")
    parts.append("Prozedere:")
    parts.append(proz)
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
        "  • Prozedere: kurz/telegraphisch, klare Bulletpoints pro Zeile; nächste Schritte, Verlauf/Kontrolle, Vorzeitige Wiedervorstellung; Medikation nur allgemein, keine erfundenen Dosierungen.\n"
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
        f"Phase='{phase}'. Wenn phase='persistent': am Ende 2–3 sinnvolle Erweiterungen beginnen mit '- Bei Persistenz/Progredienz: …'. "
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
        "- Danach fokussierte körperliche Untersuchungen, gemäss Leitsymptom.\n"
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
      - prozedere_text: ein Bulletpoint pro Zeile (nächste Schritte in der Grundversorgung, Verlauf/Kontrolle,
                        vorzeitige Wiedervorstellung (allgemein, ohne Red-Flag-Listen), Medikation nur allgemein).
        Abschlusszeile: "- Bei Persistenz/Progredienz: …".

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
        "- Beurteilung: Verdachtsdiagnose zuerst; 2–4 relevante Differenzialdiagnosen, je 1 kurze Begründung.\n"
        "- Prozedere (Hausarzt, ambulant):\n"
        "  • Zuerst Abmachungen/Empfehlungen mit dem Patienten (Schonung, Flüssigkeit, leichte Kost; "
        "    Analgetika/Antiemetika nur allgemein, ohne Dosen).\n"
        "  • Danach Termin für Verlauf/Kontrolle (konkret, z. B. 24–48 h).\n"
        "  • Danach Kriterien für vorzeitige Wiedervorstellung (allgemein, keine Red-Flag-Liste).\n"
        "  • Keine Wiederholung von in «Befunde» erhobenem/geplantem Labor/POCT/Bildgebung.\n"
        "  • Diagnostik nur in der Abschlusszeile: «- Bei Persistenz/Progredienz: …» (z. B. Bildgebung oder erweitertes Labor).\n"
        "- Schweizer Standards."
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
            raw = ask_openai(sys_msg + "\n\n" + prompt_user)  # noqa: F821  (Fallback-Helper)
            beurteilung = beurteilung or (raw.split("Prozedere:")[0].strip() if "Prozedere:" in raw else raw.strip())
            prozedere  = prozedere  or (raw.split("Prozedere:", 1)[1].strip() if "Prozedere:" in raw else "• keine Angaben")
        except Exception:
            beurteilung = beurteilung or "keine Angaben"
            prozedere  = prozedere  or "• keine Angaben"

    # Nachbearbeitung
    prozedere = _dedupe_diagnostics_in_plan(befunde or "", prozedere or "")
    prozedere = _ensure_persistence_line(
        prozedere,
        default_hint="- Bei Persistenz/Progredienz: Bildgebung oder erweitertes Labor je nach Klinik erwägen."
    )
    prozedere = _normalize_plan_bullets(prozedere)

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
