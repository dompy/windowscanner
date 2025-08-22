"""
psy_status_editor.py — Interaktiver Psychostatus-Editor (separates Modul)

Was es bietet
- Lädt die strukturierten Formulierungsbausteine aus `psychopathologischer_befund.json` (deine Liste).
- Öffnet ein eigenes Fenster mit allen Kapiteln und klickbaren "Chips" (Toggle-Buttons) für jede Formulierung.
- Button "Auto aus Anamnese" schlägt Vorauswahlen auf Basis der Freitext-/erweiterten Anamnese und eines evtl. bereits vorhandenen Status vor.
  * Verwendet, wenn möglich, dein OpenAI‑Setup aus `gpt_logic`. Fehlt ein Key oder in Offline‑Nutzung: heuristische Fallbacks.
- Live-Vorschau generiert einen Fliesstext-Befund (Schweizer Orthografie), strikt nur mit Begriffen aus der JSON.
- Rückgabe an Aufrufer: `(rendered_text, selection_state)`.

Integration in dein UI
- In `ui_assistant_stepflow.py` kannst du `from psy_status_editor import open_status_editor` importieren
  und im neuen Button-Handler den Editor öffnen. Beispiel-Handler steht am Ende dieser Datei.

Hinweise
- Keine externen Dependencies (nur tkinter/ttk).
- JSON darf verschachtelt sein (Dicts/Liten). Der Loader normalisiert das robust.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

import tkinter as tk
from tkinter import ttk, messagebox

# -------------------------------------------------------------
# JSON-Katalog laden & normalisieren
# -------------------------------------------------------------

DEFAULT_JSON_PATH = os.path.join(os.path.dirname(__file__), "psychopathologischer_befund.json")

CatalogNode = Union[List[str], Dict[str, "CatalogNode"]]


def _to_swiss(s: str) -> str:
    # Schweizer Orthografie: ß -> ss
    return s.replace("ß", "ss").replace("Äuß", "Äuss").replace("äuß", "äuss")


def load_psy_catalog(path: str = DEFAULT_JSON_PATH) -> Dict[str, Dict[str, List[str]]]:
    """Liest die Datei und gibt eine einheitliche Struktur zurück:
    { Kapitel (str): { Untergruppe (str): [terms...] } }
    Bei einfachen Listen wird die Untergruppe "Allgemein" verwendet.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data: Dict[str, CatalogNode] = json.load(f)
    except Exception as e:  # pragma: no cover
        messagebox.showerror("Katalog", f"Konnte JSON nicht laden: {e}")
        data = {}

    norm: Dict[str, Dict[str, List[str]]] = {}

    def _walk(node: CatalogNode, kapitel_name: str, pfad: List[str]):
        if isinstance(node, list):
            # Blatt → Liste von Begriffen
            kap = norm.setdefault(_to_swiss(kapitel_name), {})
            subgroup = _to_swiss(pfad[-1]) if pfad else "Allgemein"
            kap.setdefault(subgroup, [])
            for t in node:
                if isinstance(t, str):
                    kap[subgroup].append(_to_swiss(t.strip()))
            return
        if isinstance(node, dict):
            for k, v in node.items():
                _walk(v, kapitel_name, pfad + [str(k)])

    for raw_chapter, content in data.items():
        _walk(content, str(raw_chapter), [])

    # Deduplizieren + sortieren
    for kap, groups in norm.items():
        for g, terms in groups.items():
            seen = set()
            cleaned: List[str] = []
            for t in terms:
                if t and t not in seen:
                    seen.add(t)
                    cleaned.append(t)
            groups[g] = sorted(cleaned, key=lambda s: s.lower())

    return norm


# -------------------------------------------------------------
# Auswahl-State & Render-Logik
# -------------------------------------------------------------

@dataclass
class Choice:
    label: str
    group: str  # Untergruppe (z. B. "Kleidung")


@dataclass
class ChapterState:
    name: str
    # group -> set(labels)
    picks: Dict[str, set] = field(default_factory=dict)

    def toggle(self, group: str, label: str) -> None:
        s = self.picks.setdefault(group, set())
        if label in s:
            s.remove(label)
            if not s:
                # leere Gruppe entfernen
                self.picks.pop(group, None)
        else:
            s.add(label)

    def as_text(self) -> str:
        # Einfacher, gepflegter Satz wie in deinen Beispielen
        parts: List[str] = []
        for group, labels in self.picks.items():
            if not labels:
                continue
            if group == "Allgemein":
                parts.append(", ".join(sorted(labels)))
            else:
                parts.append(f"{group.lower()}: " + ", ".join(sorted(labels)))
        return "; ".join(parts)


def render_status(chapters: Dict[str, ChapterState]) -> str:
    lines: List[str] = []

    def add(name_frag: str, prefix: str):
        # Kapitel nach Fragment finden
        for k, st in chapters.items():
            if name_frag.lower() in k.lower() and st.picks:
                txt = st.as_text()
                if txt:
                    lines.append(f"{prefix} {txt}.")
                return

    # Einige prominente Kapitel mit passenden Einleitungen
    add("Erscheinungsbild", "Äusseres:")
    add("Bewusstseins", "Bewusstsein:")
    add("Orientierung", "Orientierung:")
    add("Psychomotorik", "Psychomotorik:")
    add("Denken", "Denken:")
    add("Wahrnehmungs", "Wahrnehmung:")
    add("Ich-Störungen", "Ich-Funktionen:")
    add("Stimmung", "Stimmung/Affekt:")
    add("Mnestische", "Mnestische Funktionen:")
    add("Werkzeugstörungen", "Psychische Werkzeugstörungen:")
    add("Intelligenz", "Einschätzung der Intelligenz:")
    add("Suizidalität", "Suizidalität:")

    # Rest (falls im JSON zusätzliche Kapitel vorhanden sind)
    for k, st in chapters.items():
        if any(frag in k for frag in (
            "Erscheinungsbild","Bewusstseins","Orientierung","Psychomotorik","Denken",
            "Wahrnehmungs","Ich-Störungen","Stimmung","Mnestische","Werkzeugstörungen",
            "Intelligenz","Suizidalität"
        )):
            continue
        if st.picks:
            lines.append(f"{k}: {st.as_text()}.")

    return "".join(lines).strip()


# -------------------------------------------------------------
# Heuristische & LLM-basierte Vorauswahl
# -------------------------------------------------------------

POS_HINTS = {"euthym","heiter","zugewandt","freundlich","geordnet","adäquat","ruhig","stabil","klar","voll orientiert","zeitlich","örtlich","zur Situation","zur eigenen Person"}
NEG_HINTS = {"depress","gedrückt","ängst","gereizt","labil","verflacht","unruhig","gehemmt","nicht orientiert","eingeschränkt","unsicher","halluz","wahn","zerfahren"}


def _filter_to_catalog(katalog: Dict[str, Dict[str, List[str]]], picks: Dict[str, Dict[str, List[str]]]) -> Dict[str, Dict[str, List[str]]]:
    out: Dict[str, Dict[str, List[str]]] = {}
    for chap, groups in picks.items():
        if chap not in katalog:
            # versuche fuzzy match via startswith/frag
            match = next((k for k in katalog.keys() if chap.lower() in k.lower()), None)
            if not match:
                continue
            chap = match
        out.setdefault(chap, {})
        for grp, terms in groups.items():
            if grp not in katalog[chap]:
                # evtl. Allgemein nehmen
                target_grp = grp if grp in katalog[chap] else ("Allgemein" if "Allgemein" in katalog[chap] else None)
                if not target_grp:
                    continue
            else:
                target_grp = grp
            allowed = set(katalog[chap][target_grp])
            clean = [t for t in terms if t in allowed]
            if clean:
                out[chap].setdefault(target_grp, []).extend(clean)
    return out


def suggest_from_text(context: str, katalog: Dict[str, Dict[str, List[str]]]) -> Dict[str, Dict[str, List[str]]]:
    """LLM-gestützte (wenn vorhanden) oder heuristische Vorauswahl.
    Rückgabe: {Kapitel: {Gruppe: [Term, ...]}}
    """
    context = (context or "").strip()
    if not context:
        return {}

    # 1) Versuch: LLM via gpt_logic._ask_openai_json
    try:
        from gpt_logic import _ask_openai_json as ask_json  # type: ignore
        # kompaktes Schema, Modell soll NUR Begriffe aus dem Katalog wählen
        sys = (
            "Du bist erfahrener Psychologe in der Schweiz. Wähle für einen psychopathologischen Status passende Formulierungen "
            "AUSSCHLIESSLICH aus den gegebenen Listen. Antworte als kompaktes JSON (Kapitel→Gruppe→[Begriffe]). "
            "Keine freien Texte, keine neuen Begriffe. Schweizer Orthografie."
        )
        # wir schicken nur Kapitel/Untergruppen-Namen, nicht die komplette lange Liste
        # das Modell schlägt Labels vor, wir filtern danach streng gegen den Katalog
        schema = {chap: list(groups.keys()) for chap, groups in katalog.items()}
        usr = {
            "anamnese_und_status": context[:4000],
            "schema": schema,
            "hinweis": "Wähle nur Begriffe, die exakt in den Listen vorkommen; wir verwerfen alle anderen."
        }
        raw = ask_json(messages=[
            {"role": "system", "content": sys},
            {"role": "user", "content": json.dumps(usr, ensure_ascii=False)}
        ])
        if isinstance(raw, dict):
            picks = raw if any(isinstance(v, dict) for v in raw.values()) else raw.get("auswahl", {})
            if isinstance(picks, dict):
                return _filter_to_catalog(katalog, picks)  # strikt nur erlaubte Begriffe
    except Exception:
        pass  # Fallback unten

    # 2) Fallback: ganz einfache Heuristiken über Keywords
    picks: Dict[str, Dict[str, List[str]]] = {}
    text = context.lower()

    def add(chap: str, grp: str, term: str):
        chap_key = next((k for k in katalog.keys() if chap.lower() in k.lower()), None)
        if not chap_key:
            return
        grp_key = grp if grp in katalog[chap_key] else ("Allgemein" if "Allgemein" in katalog[chap_key] else None)
        if not grp_key:
            return
        if term in katalog[chap_key][grp_key]:
            picks.setdefault(chap_key, {}).setdefault(grp_key, []).append(term)

    # Stimmung/Affekt
    if any(k in text for k in ("depress", "gedrückt", "niedergeschlagen")):
        add("Stimmung", "Allgemein", "depressiv" if "depress" in text else "niedergeschlagen")
    if any(k in text for k in ("ängst", "panik")):
        add("Stimmung", "Allgemein", "ängstlich")
    if any(k in text for k in ("heiter", "euthym")):
        add("Stimmung", "Allgemein", "euthym")

    # Orientierung
    for frag in ("zeit", "ört", "situation", "person"):
        if frag in text:
            term = {
                "zeit": "zeitlich",
                "ört": "örtlich",
                "situation": "zur Situation",
                "person": "zur eigenen Person",
            }[frag]
            add("Orientierung", "Allgemein", term)

    # Wahrnehmung
    if "halluz" in text:
        add("Wahrnehmungs", "Allgemein", "Halluzinationen")

    return picks


# -------------------------------------------------------------
# UI: Editor-Fenster
# -------------------------------------------------------------

class ScrollFrame(ttk.Frame):
    """Einfaches scrollbares Frame (y) für viele Widgets."""
    def __init__(self, master: tk.Misc):
        super().__init__(master)
        self.canvas = tk.Canvas(self, bg="#111", highlightthickness=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")


class PsyStatusEditor(tk.Toplevel):
    def __init__(self, master: tk.Misc, katalog: Dict[str, Dict[str, List[str]]], *, context_text: str = ""):
        super().__init__(master)
        self.title("Psychopathologischer Status – Editor")
        self.geometry("980x720")
        self.configure(bg="#222")
        self.result: Tuple[str, Dict[str, Dict[str, List[str]]]] = ("", {})

        self.katalog = katalog
        self.state: Dict[str, ChapterState] = {k: ChapterState(k) for k in katalog.keys()}

        # Layout
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self.left = ScrollFrame(self)
        self.left.grid(row=0, column=0, sticky="nsew", padx=(8,4), pady=8)

        right = ttk.Frame(self)
        right.grid(row=0, column=1, sticky="nsew", padx=(4,8), pady=8)
        right.rowconfigure(3, weight=1)

        ttk.Label(right, text="Vorschau (Fliesstext)", foreground="white", background="#222", anchor="w").grid(row=0, column=0, sticky="ew")
        self.preview = tk.Text(right, height=18, wrap="word", bg="#111", fg="white", insertbackground="white")
        self.preview.grid(row=1, column=0, sticky="nsew", pady=(4, 8))

        # Buttons
        btnbar = ttk.Frame(right)
        btnbar.grid(row=2, column=0, sticky="ew", pady=(0,6))
        ttk.Button(btnbar, text="Auto aus Anamnese", command=lambda: self.auto_fill(context_text)).pack(side="left")
        ttk.Button(btnbar, text="Alles zurücksetzen", command=self.reset_all).pack(side="left", padx=6)
        ttk.Button(btnbar, text="Übernehmen & Schliessen", command=self.finish).pack(side="right")

        # Kapitel rendern
        self._build_chapters()
        # ggf. direkt automatisches Prefill
        if context_text.strip():
            self.auto_fill(context_text)
        else:
            self.update_preview()

        # Modal verhalten
        self.transient(master)
        self.grab_set()

    # ------- Kapitel & Chips
    def _build_chapters(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Chip.TButton", padding=(8,2))

        for kapitel, groups in self.katalog.items():
            frame = ttk.LabelFrame(self.left.inner, text=_to_swiss(kapitel))
            frame.pack(fill="x", expand=False, pady=(6,4), padx=6)

            for group, terms in groups.items():
                row = ttk.Frame(frame)
                row.pack(fill="x", padx=6, pady=2)
                ttk.Label(row, text=group if group != "Allgemein" else "").pack(side="left", padx=(0,6))

                cloud = ttk.Frame(row)
                cloud.pack(side="left", fill="x", expand=True)

                for term in terms:
                    btn = ttk.Button(cloud, text=term, style="Chip.TButton")
                    btn_state = {"on": False}

                    def toggle(b=btn, st=btn_state, chap=kapitel, grp=group, t=term):
                        st["on"] = not st["on"]
                        self.state[chap].toggle(grp, t)
                        b.configure(style="ChipActive.TButton" if st["on"] else "Chip.TButton")
                        self.update_preview()

                    btn.configure(command=toggle)
                    btn.pack(side="left", padx=2, pady=2)

        # aktiver Chip-Stil
        style.configure("ChipActive.TButton", padding=(8,2), relief="solid")

    # ------- Auto-Fill
    def auto_fill(self, context_text: str) -> None:
        picks = suggest_from_text(context_text, self.katalog)
        # Auf State anwenden
        for chap, groups in picks.items():
            for grp, terms in groups.items():
                for t in terms:
                    self.state[chap].toggle(grp, t)
        self.update_preview()

    # ------- Utility
    def reset_all(self) -> None:
        self.state = {k: ChapterState(k) for k in self.katalog.keys()}
        self.update_preview()

    def update_preview(self) -> None:
        txt = render_status(self.state)
        self.preview.configure(state="normal")
        self.preview.delete("1.0", tk.END)
        self.preview.insert(tk.END, txt)
        self.preview.configure(state="normal")

    def finish(self) -> None:
        # exportiere aktuellen Stand als dict[str, dict[str, list[str]]]
        export: Dict[str, Dict[str, List[str]]] = {}
        for chap, st in self.state.items():
            if not st.picks:
                continue
            export[chap] = {grp: sorted(list(vals)) for grp, vals in st.picks.items()}
        self.result = (self.preview.get("1.0", tk.END).strip(), export)
        self.destroy()


# -------------------------------------------------------------
# Öffentliche API
# -------------------------------------------------------------

def open_status_editor(parent: Optional[tk.Misc] = None, *, json_path: str = DEFAULT_JSON_PATH, context_text: str = "") -> Tuple[str, Dict[str, Dict[str, List[str]]]]:
    owns_root = False
    if parent is None:
        root = tk.Tk()
        root.withdraw()
        owns_root = True
    else:
        root = parent  # type: ignore[assignment]

    katalog = load_psy_catalog(json_path)
    dlg = PsyStatusEditor(root, katalog, context_text=context_text)
    root.wait_window(dlg)
    result = getattr(dlg, "result", ("", {}))

    if owns_root:
        try:
            root.destroy()
        except Exception:
            pass
    return result


if __name__ == "__main__":
    # Manual test
    txt, state = open_status_editor(context_text="Patient wirkt gepflegt, freundlich. Eher ängstliche Grundstimmung. Voll orientiert.")
    print("--- RENDERED STATUS ---")
    print(txt)
    print("--- RAW STATE ---")
    print(json.dumps(state, ensure_ascii=False, indent=2))
