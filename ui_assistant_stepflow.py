# -*- coding: utf-8 -*-
"""
Praxis-Assistent – Tkinter GUI (robust gegen API-/Signatur-Drift)
-----------------------------------------------------------------

Fixes enthalten:
- Robust gegen unterschiedliche Backendsignaturen von
  generate_assessment_and_plan_german (Adapter mit inspect.signature)
- Alle String-Literale korrekt (keine umgebrochenen f-Strings)
- Methoden korrekt eingerückt (on_generate_full_direct war versehentlich Top-Level)
- Kleine Self-Tests für Parser/Adapter

Hinweis: In CI/Sandbox-Umgebungen ohne Tkinter bitte die CLI-Version verwenden.
Self-Test (nur Parser/Helper, ohne GUI):
  python ui_assistant_stepflow.py --self-test
"""

from __future__ import annotations
import os
import re
import sys
import random
import inspect
from typing import List, Tuple, Optional, Dict, Any

try:
    import tkinter as tk
    from tkinter import messagebox, scrolledtext
except Exception as e:
    raise SystemExit(
        "Tkinter nicht verfügbar. Starte stattdessen die CLI-Version oder installiere Tk.\n"
        "CLI: ui_assistant_stepflow_cli.py (Headless). Originalfehler: %s" % e
    )

# --- GPT-Logik ---
from gpt_logic import (
    generate_zusatzfragen_json,
    generate_befunde_gaptext_german,
    suggest_basic_exams_german,
    generate_assessment_and_plan_german,
    generate_full_entries_german,
)

# --- Red-Flags (optional) ---
try:
    from red_flags_checker import load_red_flags, check_red_flags
    HAVE_RF = True
except Exception:
    load_red_flags = None
    check_red_flags = None
    HAVE_RF = False

# =========================
#   Helper
# =========================

def _negate_german_phrase(s: str) -> str:
    s = re.sub(r"\bein(e|en|er)\b\s+", lambda m: {"e": "keine ", "en": "keinen ", "er": "keiner "}[m.group(1)], s, flags=re.I)
    if not re.search(r"\bkein|keine|keinen|keiner\b", s, re.I):
        s = "keine " + s
    return s


def _qa_to_statement(q: str, a: str) -> str:
    q = q.strip().lstrip("-•").strip()
    q = re.sub(r"\s*\?$", "", q)
    a = a.strip().lower()

    m = re.search(r"wie\s+stark\s+ist\s+die\s+(.+?)\s+auf\s+einer\s+skala\s+von\s+1\s+bis\s+10", q, re.I)
    if m and re.fullmatch(r"\d{1,2}", a):
        thema = m.group(1).strip()
        return f"{thema.capitalize()} {a}/10."

    m = re.match(r"(hatten|haben)\s+sie\s+(.*)", q, re.I)
    if m:
        rest = m.group(2).strip()
        if a in ("ja", "j", "yes"):
            return f"Hat {rest}."
        if a in ("nein", "n", "no"):
            return f"Hat {_negate_german_phrase(rest)}."
        return f"Zu '{rest}': {a}."

    if re.match(r"rauchen\s+sie\b", q, re.I):
        if a in ("ja", "j"):
            return "Raucht."
        if a in ("nein", "n"):
            return "Raucht nicht."
    if re.search(r"rauchern\s+ausgesetzt", q, re.I):
        if a in ("ja", "j"):
            return "War Rauchern ausgesetzt."
        if a in ("nein", "n"):
            return "War nicht Rauchern ausgesetzt."

    if a in ("ja", "j", "yes"):
        return f"Bejaht: {q[0].lower() + q[1:]}."
    if a in ("nein", "n", "no"):
        return f"Verneint: {q[0].lower() + q[1:]}."

    return f"{q}: {a}."


_PROTECTED_PAT = re.compile(
    r"\b(EKG|CRP|CK|CKMB|Troponin|D-?Dimer|BNP|NTproBNP|HbA1c|Na|K|mmHg|mg|ml|COPD|ASTHMA|COVID|TIA|CVA|CT|MRI|RX|O2|SpO2|POCT)\b",
    re.IGNORECASE,
)


def _is_suitable_token(tok: str) -> bool:
    if not tok or len(tok) < 5:
        return False
    if _PROTECTED_PAT.search(tok):
        return False
    if any(ch.isdigit() for ch in tok):
        return False
    if tok.isupper():
        return False
    if "-" in tok or "/" in tok:
        return False
    return True


def inject_typos(text: str, max_typos: int = 2) -> str:
    if not text or max_typos <= 0:
        return text or ""
    rng = random.Random(hash(text) & 0xFFFFFFFF)
    tokens = text.split(" ")
    idxs = [i for i, t in enumerate(tokens) if _is_suitable_token(re.sub(r"[^\wÄÖÜäöüß]", "", t))]
    rng.shuffle(idxs)
    typos = 0
    for i in idxs:
        tok = tokens[i]
        letters = list(tok)
        positions = [j for j in range(len(letters) - 1) if letters[j].isalpha() and letters[j + 1].isalpha()]
        if not positions:
            continue
        j = rng.choice(positions)
        letters[j], letters[j + 1] = letters[j + 1], letters[j]
        tokens[i] = "".join(letters)
        typos += 1
        if typos >= max_typos:
            break
    return " ".join(tokens)


def parse_gap_qa(text: str) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    for line in (text or "").splitlines():
        line = line.strip().lstrip("-• ").strip()
        if not line:
            continue
        m = re.match(r"(.+?)[\?:]\s*(ja|nein|j|n|yes|no|\d{1,2})\s*$", line, re.I)
        if m:
            q, a = m.group(1).strip(), m.group(2).strip()
            if not q.endswith("?"):
                q += "?"
            pairs.append((q, a))
    return pairs


# ---- Fokus-Erkennung & Befunde-Lückentext (lokal, ohne LLM) ----

def _guess_focus(anamnese_text: str, qa_pairs: Optional[List[Tuple[str, str]]] = None) -> str:
    """Leitsymptom grob erkennen (für objektiven Befunde-Lückentext)."""
    text = (anamnese_text or "").lower()
    if qa_pairs:
        text += " " + " ".join((q + " " + a).lower() for q, a in qa_pairs)

    # Muskuloskelettal
    if any(k in text for k in ("handgelenk", "hand", "finger", "daumen", "wrist", "karpal", "karpaltunnel")):
        return "msk_wrist"
    if any(k in text for k in ("schulter", "ac-gelenk", "clavicula", "delta")):
        return "msk_shoulder"
    if any(k in text for k in ("knie", "menisk", "kreuzband", "patella")):
        return "msk_knee"
    if any(k in text for k in ("sprunggelenk", "fuss", "fuß", "umknick", "achill")):
        return "msk_ankle"
    if any(k in text for k in ("ellenbogen", "ellbogen", "epicondyl")):
        return "msk_elbow"
    if any(k in text for k in ("hüfte", "huefte", "leiste", "cox")):
        return "msk_hip"

    # Wirbelsäule / Rücken
    if any(k in text for k in ("rücken", "ruecken", "lumb", "lws", "ischias")):
        return "lws"

    # Innere
    if any(k in text for k in ("brust", "thorax", "atemnot", "brustschmerz")):
        return "thorax"
    if any(k in text for k in ("bauch", "abdomen", "übelkeit", "erbrechen")):
        return "abdomen"
    if any(k in text for k in ("hals", "husten", "halsweh", "halsschmerz")):
        return "hno"
    if any(k in text for k in ("kopfschmerz", "schwindel", "synkope")):
        return "neuro"
    return "allg"


def _extract_cues(anamnese_text: str, qa_pairs: Optional[List[Tuple[str, str]]] = None) -> Dict[str, bool]:
    """Einfaches Signal-Set aus Anamnese/Q&A ableiten, um die Untersuchung zu fokussieren."""
    t = (anamnese_text or "").lower()
    if qa_pairs:
        t += " " + " ".join((q + " " + a).lower() for q, a in qa_pairs)

    def has(words: List[str]) -> bool:
        return any(w in t for w in words)

    return {
        "trauma": has(["sturz", "gestürzt", "gefallen", "trauma", "umknick", "verdreht", "prellung"]),
        "overuse": has(["überlast", "ueberlast", "handwerk", "repetitiv", "tastatur", "werkzeug", "sport"]),
        "swelling": has(["schwellung", "geschwollen"]),
        "redness_heat": has(["rötung", "roetung", "heiss", "wärme", "warm"]),
        "numbness": has(["taub", "kribbel", "einschlaf", "parästhes", "paraesthes"]),
        "weakness": has(["schwäche", "kraftverlust"]),
        "radial": has(["daumen", "radial", "speiche", "tabatiere", "snuffbox"]),
        "ulnar": has(["ulnar", "elle", "kleinfinger"]),
        "chronic": has(["wochen", "monaten"]),
        "acute": has(["heute", "gestern", "seit gestern", "akut"]),
    }


def _build_exam_lueckentext(focus: str = "allg", phase: str = "initial", cues: Optional[Dict[str, bool]] = None) -> str:
    """Erzeugt einen **Befunde**-Lückentext (objektive Untersuchung + ggf. POCT) nach Leitsymptom.
    `cues` verfeinert die Auswahl (z. B. Trauma → Snuffbox-Test, Kribbeln → Phalen/Tinel).
    """
    cues = cues or {}
    blocks: List[str] = []
    # Kurzstatus immer zuerst (ohne führendes "- ", das fügen wir unten ein)
    blocks.append("AZ: wach, orientiert, kooperativ; Haut/Schleimhäute: ____; Temp: ____")

    if focus == "msk_wrist":
        blocks += [
            "Hand/Handgelenk: Inspektion ____ (Schwellung/Rötung/Fehlstellung/Atrophie)",
            "Palpation: Tabatière/Os scaphoideum ____; distaler Radius/Ulna ____; DRUG ____; Sehnenscheiden ____",
            "Beweglichkeit: Flex ____/Ext ____/Ulnardev ____/Radialdev ____; Pro-/Supination ____",
            "Neurovaskulär: Sensibilität N. medianus/ulnaris/radialis ____; Motorik Daumenopposition/Abduktion ____; Kapillarfüllung/A. radialis/ulnaris ____ (Vergleich Gegenseite)",
        ]
        spec: List[str] = []
        if cues.get("trauma"):
            spec.append("Spezial: Snuffbox-Druckschmerz ____; axialer Daumenkompressionsschmerz ____; Stabilität DRUG ____")
        if cues.get("overuse") or cues.get("radial"):
            spec.append("Spezial: Finkelstein-Test (de Quervain) ____; EPL/ECU-Sehne ____")
        if cues.get("numbness"):
            spec.append("Spezial: Phalen-/Tinel-Zeichen (CTS) ____")
        if cues.get("ulnar"):
            spec.append("Spezial: TFCC-Belastungstest ____")
        if spec:
            blocks += spec
        poct = "POCT: — (keine); Ultraschall falls verfügbar."
        persist = "Bildgebung bei Persistenz/Progredienz (Rx Handgelenk inkl. Skaphoid-Serie oder US), ggf. Ortho/Handchirurgie."

    elif focus == "lws":
        blocks += [
            "Wirbelsäule/LWS: Inspektion ____; Palpation ____ (paravertebral/Processi spinosi); Klopfschmerz ____",
            "Beweglichkeit LWS: Flex/Ex/Lat/Rotation ____ (eingeschränkt/seitengleich)",
            "Neurologie Beine: Kraft ____; Reflexe (PSR/ASR) ____; Sensibilität ____; Lasègue ____",
            "Gangbild/Zehen-Fersenstand ____",
        ]
        poct = "POCT (bei Bedarf): CRP, Urin-Stix; ggf. BZ."
        persist = "Bei Persistenz/Progredienz: Rx LWS oder MRI je nach Klinik; evtl. Überweisung Physio/Ortho/Neurologie."

    elif focus == "thorax":
        blocks += [
            "Herz: Frequenz/Rhythmus ____; Auskultation ____",
            "Lunge: AF ____; Auskultation ____ (vesikulär/Knisterrasseln/Giemen)",
            "Thoraxwand: Druck-/Bewegungsschmerz ____",
        ]
        poct = "POCT (bei Bedarf): SpO2, EKG, CRP."
        persist = "Bei Persistenz/Progredienz: Rx Thorax; EKG/Labor nach Klinik."

    elif focus == "abdomen":
        blocks += [
            "Abdomen: Inspektion ____; Abwehrspannung ____; Druckdolenz ____; Klopfschmerz ____",
            "Darmgeräusche ____; McBurney/Murphy ____",
        ]
        poct = "POCT (bei Bedarf): Urin-Stix, CRP, BZ."
        persist = "Bei Persistenz/Progredienz: Labor/US Abdomen; evtl. Überweisung."

    elif focus == "hno":
        blocks += [
            "Hals/Nase/Rachen: Rötung/Beläge ____; Lymphknoten ____",
            "Ohren: Trommelfell ____; Nase: Sekretion ____",
            "Lunge (Auskultation) ____",
        ]
        poct = "POCT (bei Bedarf): Strep-/Influenza-/COVID-Ag."
        persist = "Bei Persistenz/Progredienz: Labor/Rx je nach Klinik; HNO-Überweisung erwägen."

    elif focus == "neuro":
        blocks += [
            "Neuro-Status kurz: Vigilanz/Orientierung ____; Paresen ____; Sensibilität ____; Hirnnerven ____",
            "Koordination/Gangbild ____",
        ]
        poct = "POCT (bei Bedarf): BZ; ggf. EKG."
        persist = "Bei Persistenz/Progredienz: Neuro-Abklärung; Bildgebung nach Klinik."

    else:  # allg
        blocks += [
            "Herz: RR/Puls ____; Auskultation ____",
            "Lunge: AF ____; Auskultation ____",
            "Abdomen: weich/nicht druckdolent ____",
        ]
        poct = "POCT (bei Bedarf): CRP, BZ, Urin-Stix."
        persist = "Bei Persistenz/Progredienz: weiterführende Abklärung nach Klinik."

    # Checkliste unten anhängen

    checklist = [
        "Untersuchung komplett, Seitenvergleich",
        poct,
    ]

    # Stringaufbau in kleinen Schritten, damit Editoren nichts umbrechen
    lines = ["- " + line if not line.startswith("-") else line for line in blocks]
    lz = "\n".join(lines)

    checklist_str = "\n- [ ] ".join(checklist)
    lz += "\n- [ ] " + checklist_str

    if phase == "persistent":
        lz += "\nBei Persistenz/Progredienz: " + persist

    return lz


# =========================
#   UI-Klasse
# =========================

class ConsultationAssistant:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("🧠 Praxis-Assistent")
        self.root.geometry("1000x820")
        self.root.configure(bg="#222")

        # State
        self.fields: Dict[str, scrolledtext.ScrolledText] = {}
        self.last_zusatzfragen: List[str] = []
        self.last_zusatzfragen_qa: List[Tuple[str, str]] = []
        self.humanize_var = tk.BooleanVar(value=False)

        # Layout
        self._label("Anamnese (frei)")
        self.fields["Anamnese"] = self._text(height=6)

        self._button("1) Zusatzfragen erzeugen", self.on_gaptext)
        self._label("Anamnese – Lückentext (editierbar)")
        self.txt_gap = self._text(height=6)
        self.fields["Anamnese – Lückentext (editierbar)"] = self.txt_gap
        tk.Button(self.root, text="Antworten → Fliesstext", command=self.on_anamnese_answers_to_narrative).pack(padx=8, pady=(2, 0), anchor="w")

        toolbar = tk.Frame(self.root, bg="#222")
        toolbar.pack(fill="x", padx=8, pady=(6, 4))
        tk.Button(toolbar, text="2) Befunde (Lückentext, Basis)", command=self.on_befunde_gaptext).pack(side="left", padx=4)
        tk.Button(toolbar, text="➕ Mehr (bei Persistenz)", command=lambda: self.on_befunde_gaptext(phase="persistent")).pack(side="left", padx=4)
        tk.Button(toolbar, text="Basis-Untersuchungen (Freitext)", command=self.on_basic_exams).pack(side="left", padx=12)

        self._label("Befunde (werden überschrieben)")
        self.fields["Befunde"] = self._text(height=6)

        tk.Checkbutton(self.root, text="Humanisieren (Tippfehler)", variable=self.humanize_var, bg="#222", fg="white", selectcolor="#333").pack(padx=8, anchor="w")
        self._button("3) Beurteilung + Prozedere finalisieren", self.on_assessment_and_plan)

        cols = tk.Frame(self.root, bg="#222")
        cols.pack(fill="both", expand=True, padx=8)
        left = tk.Frame(cols, bg="#222"); left.pack(side="left", fill="both", expand=True, padx=(0, 4))
        right = tk.Frame(cols, bg="#222"); right.pack(side="left", fill="both", expand=True, padx=(4, 0))
        tk.Label(left, text="Beurteilung", fg="white", bg="#222", anchor="w").pack(fill="x")
        self.fields["Beurteilung"] = self._text(parent=left, height=8)
        tk.Label(right, text="Prozedere", fg="white", bg="#222", anchor="w").pack(fill="x")
        self.fields["Prozedere"] = self._text(parent=right, height=8)

        self._label("⚠️ Red Flags (Info, nicht in den Feldern)")
        self.txt_redflags = self._text(height=4)
        self.txt_redflags.configure(state="disabled")

        util = tk.Frame(self.root, bg="#222")
        util.pack(fill="x", padx=8, pady=(6, 0))
        tk.Button(util, text="Alles generieren (4 Felder)", command=self.on_generate_full_direct).pack(side="left", padx=4)
        tk.Button(util, text="Gesamtausgabe kopieren", command=self.copy_output).pack(side="left", padx=4)
        tk.Button(util, text="Reset", command=self.reset_all).pack(side="left", padx=4)

        self._label("Gesamtausgabe (kopierfertig)")
        self.output_full = self._text(height=10)

    # --- UI helpers ---
    def _label(self, text: str):
        tk.Label(self.root, text=text, fg="white", bg="#222", anchor="w", font=("Arial", 10, "bold")).pack(fill="x", padx=8, pady=(8, 0))

    def _text(self, height=6, parent=None):
        parent = parent or self.root
        t = scrolledtext.ScrolledText(parent, height=height, wrap=tk.WORD, bg="#111", fg="white", insertbackground="white")
        t.pack(fill="both", expand=False, padx=8, pady=(4, 0))
        return t

    def _button(self, label, cmd):
        tk.Button(self.root, text=label, command=cmd).pack(padx=8, pady=(6, 0), anchor="w")

    # --- Actions ---
    def _call_assess_plan_adapter(self, anamnese: str, befunde: str):
        """Backend-Aufruf robust an verschiedene Signaturen anpassen."""
        fn = generate_assessment_and_plan_german
        try:
            sig = inspect.signature(fn)
        except Exception:
            sig = None

        args: List[Any] = [anamnese]
        kwargs: Dict[str, Any] = {}

        # Optionale Keywords, falls vorhanden
        if sig and "zusatzfragen" in sig.parameters:
            kwargs["zusatzfragen"] = self.last_zusatzfragen
        if sig and "zusatzfragen_qa" in sig.parameters:
            kwargs["zusatzfragen_qa"] = (self.last_zusatzfragen_qa or None)
        if sig and "humanize" in sig.parameters:
            kwargs["humanize"] = bool(self.humanize_var.get())

        # Prüfen, ob zweites Pflicht-Positional benötigt wird
        if sig:
            params = list(sig.parameters.values())
            required_pos = [p for p in params if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                                            inspect.Parameter.POSITIONAL_OR_KEYWORD)
                            and p.default is inspect._empty]
            if len(required_pos) >= 2:
                args.append(befunde)
            else:
                # Eventuelles optionales Befunde-Keyword füllen
                for cand in ("befunde_final", "befunde", "befunde_text", "befunde_raw"):
                    if cand in sig.parameters and sig.parameters[cand].default is not inspect._empty:
                        kwargs[cand] = befunde
                        break

        # Versuch 1: mit erkannten Args/Kwargs
        try:
            return fn(*args, **kwargs)
        except TypeError:
            # Versuch 2: nur Anamnese
            try:
                return fn(anamnese)
            except TypeError:
                # Versuch 3: Anamnese + Befunde
                return fn(anamnese, befunde)

    def _refresh_qa_from_gap(self):
        gap = self.txt_gap.get("1.0", tk.END)
        self.last_zusatzfragen_qa = parse_gap_qa(gap)

    def on_gaptext(self):
        raw = self.fields["Anamnese"].get("1.0", tk.END).strip()
        if not raw:
            messagebox.showwarning("Hinweis", "Bitte zuerst Anamnese (frei) eingeben.")
            return
        try:
            payload = generate_zusatzfragen_json(raw)
            self.last_zusatzfragen = payload.get("zusatzfragen", [])[:5]
        except Exception as e:
            messagebox.showerror("Fehler", f"Zusatzfragen fehlgeschlagen:\n{e}")
            return
        self.txt_gap.delete("1.0", tk.END)
        for q in self.last_zusatzfragen:
            self.txt_gap.insert(tk.END, f"- {q}\n")

    def on_anamnese_answers_to_narrative(self):
        fld_free = self.fields.get("Anamnese")
        fld_edit = self.txt_gap
        if not fld_edit:
            print("⚠️ Lückentext-Feld nicht gefunden.")
            return
        free_txt = (fld_free.get("1.0", "end").strip() if fld_free else "").strip().rstrip(".")
        lines = [ln.strip() for ln in fld_edit.get("1.0", "end").splitlines() if ln.strip()]
        statements: List[str] = []
        for ln in lines:
            m = re.match(r"^\s*[-•]?\s*(.+?)\s+(ja|nein|j|n|yes|no|\d{1,2})\s*$", ln, flags=re.I)
            if m:
                q, a = m.group(1), m.group(2)
                s = _qa_to_statement(q, a)
                if s:
                    statements.append(s)
        if not statements:
            print("ℹ️ Keine beantworteten Zeilen gefunden (am Zeilenende 'ja'/'nein' oder Zahl).")
            return
        base = (free_txt[0].upper() + free_txt[1:] + ".") if free_txt else ""
        body = " ".join(statements)
        final = (base + " " + body).strip()
        fld_edit.delete("1.0", "end")
        fld_edit.insert("1.0", final)

    def on_befunde_gaptext(self, phase: str = "initial"):
        """Erzeugt **Befunde** als objektiven Lückentext (körperliche Untersuchung + ggf. POCT).
        Subjektive Inhalte bleiben in der Anamnese.
        """
        self._refresh_qa_from_gap()
        anamnese_for_exams = self.fields.get("Anamnese").get("1.0", tk.END).strip()
        if not anamnese_for_exams:
            messagebox.showwarning("Hinweis", "Keine Anamnese vorhanden.")
            return
        # Leitsymptom schätzen und lokalen Lückentext bauen (ohne LLM)
        focus = _guess_focus(anamnese_for_exams, self.last_zusatzfragen_qa)
        cues = _extract_cues(anamnese_for_exams, self.last_zusatzfragen_qa)
        bef_text = _build_exam_lueckentext(focus, phase, cues)
        self.fields["Befunde"].delete("1.0", tk.END)
        self.fields["Befunde"].insert(tk.END, bef_text or "")

    def on_basic_exams(self, phase: str = "initial"):
        self._refresh_qa_from_gap()
        anamnese_for_exams = self.fields["Anamnese"].get("1.0", tk.END).strip()
        if not anamnese_for_exams:
            messagebox.showwarning("Hinweis", "Keine Anamnese vorhanden.")
            return
        try:
            try:
                bef = suggest_basic_exams_german(
                    anamnese_for_exams,
                    phase=phase,
                    zusatzfragen=self.last_zusatzfragen,
                    zusatzfragen_qa=self.last_zusatzfragen_qa or None,
                )
            except TypeError:
                bef = suggest_basic_exams_german(anamnese_for_exams, phase=phase)
        except Exception as e:
            messagebox.showerror("Fehler", f"Untersuchungen fehlgeschlagen:\n{e}")
            return
        self.fields["Befunde"].delete("1.0", tk.END)
        self.fields["Befunde"].insert(tk.END, bef)

    def on_assessment_and_plan(self):
        self._refresh_qa_from_gap()
        anamnese = self.fields["Anamnese"].get("1.0", tk.END).strip()
        befunde = self.fields["Befunde"].get("1.0", tk.END).strip()
        if not anamnese:
            messagebox.showwarning("Hinweis", "Keine Anamnese vorhanden.")
            return
        try:
            assess, plan = self._call_assess_plan_adapter(anamnese, befunde)
        except Exception as e:
            messagebox.showerror("Fehler", f"Beurteilung/Prozedere fehlgeschlagen:\n{e}")
            return

        if self.humanize_var.get():
            assess = inject_typos(assess, max_typos=2)
            plan = inject_typos(plan, max_typos=2)

        self.fields["Beurteilung"].delete("1.0", tk.END)
        self.fields["Beurteilung"].insert(tk.END, (assess or "").strip() + "\n")
        self.fields["Prozedere"].delete("1.0", tk.END)
        self.fields["Prozedere"].insert(tk.END, (plan or "").strip() + "\n")

    def on_generate_full_direct(self):
        self._refresh_qa_from_gap()
        parts: List[str] = []
        anamnese_raw = self.fields["Anamnese"].get("1.0", tk.END).strip()
        gap = self.txt_gap.get("1.0", tk.END).strip()
        if anamnese_raw:
            parts.append("Anamnese: " + anamnese_raw)
        if gap:
            parts.append("Zusatzfragen: " + gap)
        combined = "\n\n".join(parts).strip()
        if not combined:
            messagebox.showwarning("Hinweis", "Bitte Anamnese im Tool eingeben.")
            return
        try:
            payload, full_block = generate_full_entries_german(combined, context={"humanize": self.humanize_var.get()})
        except Exception as e:
            messagebox.showerror("Fehler", f"Generierung fehlgeschlagen:\n{e}")
            return
        self.fields["Anamnese"].delete("1.0", tk.END)
        self.fields["Anamnese"].insert(tk.END, payload.get("anamnese_text", ""))
        self.fields["Befunde"].delete("1.0", tk.END)
        self.fields["Befunde"].insert(tk.END, payload.get("befunde_text", ""))
        self.fields["Beurteilung"].delete("1.0", tk.END)
        self.fields["Beurteilung"].insert(tk.END, payload.get("beurteilung_text", ""))
        self.fields["Prozedere"].delete("1.0", tk.END)
        self.fields["Prozedere"].insert(tk.END, payload.get("prozedere_text", ""))
        # Red Flags
        self.update_red_flags(payload.get("anamnese_text", ""), payload.get("befunde_text", ""))
        # Gesamtausgabe
        self.output_full.delete("1.0", tk.END)
        self.output_full.insert(tk.END, full_block)

    # --- Red Flags & Output ---
    def update_red_flags(self, anamnese_text: str, befunde_text: str):
        rf_list: List[str] = []
        if HAVE_RF and load_red_flags and check_red_flags:
            try:
                here = os.path.dirname(os.path.abspath(__file__))
                path = os.path.join(here, "red_flags.json")
                if os.path.exists(path):
                    data = load_red_flags(path)
                    rf_hits = check_red_flags(anamnese_text + "\n" + befunde_text, data, return_keywords=True) or []
                    rf_list = [f"{kw} – {msg}" for (kw, msg) in rf_hits]
            except Exception:
                rf_list = []
        self.set_red_flags(rf_list)

    def set_red_flags(self, items: List[str]):
        self.txt_redflags.configure(state="normal")
        self.txt_redflags.delete("1.0", tk.END)
        if items:
            self.txt_redflags.insert(tk.END, "\n".join(f"- {x}" for x in items))
        self.txt_redflags.configure(state="disabled")

    def build_output(self, anamnese: str, befunde: str, beurteilung: str, prozedere: str):
        parts = [
            "Anamnese:", anamnese or "keine Angaben", "",
            "Befunde:", befunde or "keine Angaben", "",
            "Beurteilung:", beurteilung or "keine Angaben", "",
            "Prozedere:", prozedere or "keine Angaben",
        ]
        self.output_full.delete("1.0", tk.END)
        self.output_full.insert(tk.END, "\n".join(parts).strip())

    def copy_output(self):
        text = self.output_full.get("1.0", tk.END)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        messagebox.showinfo("Kopiert", "Gesamtausgabe in Zwischenablage.")

    def reset_all(self):
        for k in ("Anamnese", "Befunde", "Beurteilung", "Prozedere"):
            self.fields[k].delete("1.0", tk.END)
        self.txt_gap.delete("1.0", tk.END)
        self.output_full.delete("1.0", tk.END)
        self.set_red_flags([])
        self.last_zusatzfragen = []
        self.last_zusatzfragen_qa = []


# =========================
#   Boot & Self-Tests
# =========================

def _self_tests() -> int:
    print("Running self-tests (helpers + adapter)…")
    # 1) QA-Parser
    qa_text = (
        "Haben Sie Atemnot? ja\n"
        "Rauchen Sie: nein\n"
        "Wie stark ist die Schmerzintensität auf einer Skala von 1 bis 10? 7"
    )
    pairs = parse_gap_qa(qa_text)
    assert len(pairs) == 3, "QA-Parser sollte 3 Paare liefern"

    # 2) Typos schützen Fachkürzel
    s = "EKG normal, CRP 12 mg/l, Thoraxschmerz seit gestern"
    out = inject_typos(s, 2)
    assert "EKG" in out and "CRP" in out, "Protected tokens verändert"

    # 3) Adapter: simuliere verschiedene Signaturen
    def f1(anamnese):
        return ("A1", "P1")
    def f2(anamnese, befunde_final):
        return ("A2:" + befunde_final[:5], "P2")
    def f3(anamnese, *, humanize=False):
        return ("A3" + ("h" if humanize else ""), "P3")
    def f4(anamnese, zusatzfragen=None, zusatzfragen_qa=None, humanize=False):
        return ("A4" + ("+z" if zusatzfragen else ""), "P4")

    # Monkeypatch-Aufruf über Adapter-Logik
    app_dummy = type("D", (), {})()
    def run_adapter(fn):
        # minimaler Dummy-State
        app_dummy.last_zusatzfragen = ["Q1"]
        app_dummy.last_zusatzfragen_qa = [("Frage?", "ja")]
        app_dummy.humanize_var = type("V", (), {"get": lambda self: True})()
        return ConsultationAssistant._call_assess_plan_adapter.__get__(app_dummy, type(app_dummy))("ANA", "BEF")

    # Wir ersetzen zur Laufzeit die globale Referenz und rufen den Adapter
    global generate_assessment_and_plan_german
    generate_assessment_and_plan_german = f1; a,p = run_adapter(f1); assert a=="A1" and p=="P1"
    generate_assessment_and_plan_german = f2; a,p = run_adapter(f2); assert a.startswith("A2:")
    generate_assessment_and_plan_german = f3; a,p = run_adapter(f3); assert a=="A3h"
    generate_assessment_and_plan_german = f4; a,p = run_adapter(f4); assert a.startswith("A4+")

    print("Self-tests OK.")
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        raise SystemExit(_self_tests())
    root = tk.Tk()
    app = ConsultationAssistant(root)
    root.mainloop()


if __name__ == "__main__":
    main()
