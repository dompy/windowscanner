"""Tkinter-UI – Hausarzt-Version (clean)
- Fragt beim Start den OPENAI_API_KEY per Dialog ab (maskiert) und speichert ihn prozesslokal; unter Windows optional persistent via setx.
- In allen Actions (Buttons) Retry-Logik: Fehlt der Key oder ist er verloren gegangen, wird er nachgefordert und der jeweilige Call einmalig erneut ausgeführt.
- Red Flags werden separat angezeigt (medizinische Regeln bevorzugt).
- Unterstützt einen Headless-Smoketest via "--smoke-test" (für CI).
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from typing import Callable, Optional, Tuple

import tkinter as tk
from tkinter import messagebox, scrolledtext, simpledialog, ttk

from gpt_logic import (
    generate_anamnese_gaptext_german,
    generate_assessment_and_plan_german,
    generate_status_gaptext_german,
    generate_full_entries_german,
    resolve_red_flags_path,
)

try:
    from red_flags_checker import check_red_flags, load_red_flags
except Exception:
    load_red_flags = None  # type: ignore
    check_red_flags = None  # type: ignore


# ---------- Utility: API-Key sicherstellen ----------

def ensure_api_key(parent: tk.Tk) -> bool:
    """Sichert, dass OPENAI_API_KEY vorhanden ist.

    - Fragt bei Bedarf per Dialog ab (maskiert).
    - Setzt ihn prozesslokal (sofort wirksam) und unter Windows zusätzlich persistent via setx (best effort).
    - Gibt True zurück, wenn ein Key verfügbar ist, sonst False.
    """
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if key:
        return True

    key = simpledialog.askstring(
        "OpenAI API Key",
        "Bitte OpenAI API Key eingeben:",
        show="*",
        parent=parent,
    )
    if not key:
        messagebox.showwarning("Fehlender Key", "Ohne OpenAI API Key kann die App nicht arbeiten.")
        return False

    # Prozesslokal setzen
    os.environ["OPENAI_API_KEY"] = key

    # Unter Windows persistent speichern (optional)
    try:
        if os.name == "nt":
            subprocess.run(["setx", "OPENAI_API_KEY", key], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    return True


# ---------- Headless-Smoke-Test (für CI/Actions) ----------

def _smoke_test() -> int:
    try:
        p = resolve_red_flags_path(prefer_psych=False)
        print(
            json.dumps(
                {
                    "ok": True,
                    "platform": platform.platform(),
                    "red_flags_path_exists": os.path.exists(p),
                    "red_flags_path": p,
                    "has_api_key": bool(os.getenv("OPENAI_API_KEY")),
                }
            )
        )
        return 0
    except Exception as e:  # pragma: no cover
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1


def _subheading(self, parent: tk.Misc, text: str):
    tk.Label(parent, text=text, fg="white", bg="#222", anchor="w",
             font=("Arial", 10, "bold")).pack(fill="x")

class ConsultationAssistant:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("🩺 Pezzi PsyMate")
        self.root.geometry("1000x820")
        self.root.configure(bg="#222")

        # ttk-Theme & Button-Style (flach, cross-platform)
        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")   # erlaubt Farbanpassungen auch auf macOS
        except Exception:
            pass

        self.style.configure(
            "Primary.TButton",
            padding=(10, 4),
            borderwidth=0,
            focusthickness=0,
            foreground="white",
            background="#333"
        )
        self.style.map(
            "Primary.TButton",
            background=[("active", "#444"), ("pressed", "#222"), ("!disabled", "#333")],
            relief=[("pressed", "flat"), ("!pressed", "flat")]
        )

        self.fields: dict[str, scrolledtext.ScrolledText] = {}

        # Anamnese (frei)
        self._label("Anamnese")
        self.fields["Anamnese"] = self._text(height=6)

        # Zusatzfragen
        self._button("Anamnese erweitern", self.on_gaptext)
        self._label("Erweiterte Anamnese")
        self.txt_gap = self._text(height=6)

        # status
        bar = tk.Frame(self.root, bg="#222")
        bar.pack(fill="x", padx=8, pady=(6, 4))
        self._button("Status", lambda: self.on_status_gaptext("initial"), parent=bar)

        self._label("Status (wird überschrieben)")
        self.fields["Status"] = self._text(height=7)

        # Einschätzung + Prozedere
        self._button("Einschätzung + Prozedere", self.on_finalize)

        cols = tk.Frame(self.root, bg="#222")
        cols.pack(fill="both", expand=True, padx=8)
        left = tk.Frame(cols, bg="#222")
        left.pack(side="left", fill="both", expand=True, padx=(0, 4))
        right = tk.Frame(cols, bg="#222")
        right.pack(side="left", fill="both", expand=True, padx=(4, 0))

        self._label("Einschätzung", parent=left)
        self.fields["Einschätzung"] = self._text(parent=left, height=8)

        self._label("Empfohlenes Prozedere", parent=right)
        self.fields["Prozedere"] = self._text(parent=right, height=8)


        # Red Flags
        self._label("⚠️ Red Flags (Info, nicht in den Feldern)")
        self.txt_redflags = self._text(height=4)
        self.txt_redflags.configure(state="disabled")

        # Utilities
        util = tk.Frame(self.root, bg="#222")
        util.pack(fill="x", padx=8, pady=(6, 0))
        self._button("Alles generieren", self.on_generate_full_direct, parent=util, side="left")
        self._button("Gesamtausgabe kopieren", self.copy_output, parent=util, side="left")
        self._button("Reset", self.reset_all, parent=util, side="left")

        self._label("Gesamtausgabe")
        self.output_full = self._text(height=10)

    # ---------- UI helpers ----------
    def _label(self, text: str, parent: Optional[tk.Misc] = None, size: int = 10):
        parent = parent or self.root
        tk.Label(
            parent, text=text, fg="white", bg="#222", anchor="w",
            font=("Arial", size, "bold")
        ).pack(fill="x", padx=8 if parent is self.root else 0, pady=(8, 0))


    def _text(self, height=6, parent: Optional[tk.Misc] = None) -> scrolledtext.ScrolledText:
        parent = parent or self.root
        t = scrolledtext.ScrolledText(parent, height=height, wrap=tk.WORD, bg="#111", fg="white", insertbackground="white")
        t.pack(fill="both", expand=False, padx=8, pady=(4, 4))
        return t

    def _button(self, label: str, cmd: Callable[[], None],
                parent: Optional[tk.Misc] = None, side: Optional[str] = None):
        parent = parent or self.root
        btn = ttk.Button(parent, text=label, command=cmd, style="Primary.TButton")
        pad_x = 8 if parent is self.root else 4
        if side:
            btn.pack(side=side, padx=pad_x, pady=(6, 0), anchor="w")
        else:
            btn.pack(padx=pad_x, pady=(6, 0), anchor="w")
        return btn


    # ---------- Gemeinsamer Retry-Wrapper ----------
    def _call_with_key_retry(self, action_name: str, fn: Callable[[], Tuple[Optional[dict], Optional[str]] | Tuple[str, str] | str | None]):
        """Führt einen OpenAI-Call aus und fragt bei fehlendem Key einmalig nach (Retry)."""
        try:
            return fn()
        except EnvironmentError as e:
            # typischer Text aus gpt_logic._get_openai_client()
            if "OPENAI_API_KEY" in str(e):
                if ensure_api_key(self.root):
                    try:
                        return fn()
                    except Exception as e2:  # zweiter Fehler → zeigen
                        messagebox.showerror("Fehler", f"{action_name} fehlgeschlagen:\n{e2}")
                        return None
                else:
                    return None
            messagebox.showerror("Fehler", f"{action_name} fehlgeschlagen:\n{e}")
            return None
        except Exception as e:
            messagebox.showerror("Fehler", f"{action_name} fehlgeschlagen:\n{e}")
            return None

    # ---------- Actions ----------
    def on_gaptext(self):
        raw = self.fields["Anamnese"].get("1.0", tk.END).strip()
        if not raw:
            messagebox.showwarning("Hinweis", "Bitte zuerst Anamnese eingeben.")
            return

        def _do():
            payload, gap = generate_anamnese_gaptext_german(raw)
            return payload, gap

        result = self._call_with_key_retry("Lückentext", _do)
        if not result:
            return
        payload, gap = result  # type: ignore[misc]
        self.txt_gap.delete("1.0", tk.END)
        self.txt_gap.insert(tk.END, gap or "")

    def on_status_gaptext(self, phase: str = "initial"):
        gap = self.txt_gap.get("1.0", tk.END).strip() if hasattr(self, "txt_gap") else ""
        anamnese_src = gap or self.fields.get("Anamnese").get("1.0", tk.END).strip()
        if not anamnese_src:
            messagebox.showwarning("Hinweis", "Keine Anamnese vorhanden.")
            return

        def _do():
            payload, bef_text = generate_status_gaptext_german(anamnese_src, phase=phase)
            return payload, bef_text

        result = self._call_with_key_retry("status-Lückentext", _do)
        if not result:
            return
        payload, bef_text = result  # type: ignore[misc]

        if phase == "initial":
            self.fields["Status"].delete("1.0", tk.END)
            self.fields["Status"].insert(tk.END, bef_text or "")
        else:
            current = self.fields["Status"].get("1.0", tk.END).strip()
            if current:
                self.fields["Status"].insert(tk.END, "\n\n")
            self.fields["Status"].insert(tk.END, bef_text or "")

    def on_finalize(self):
        anamnese_final = self.txt_gap.get("1.0", tk.END).strip() or self.fields["Anamnese"].get("1.0", tk.END).strip()
        status_final = self.fields["Status"].get("1.0", tk.END).strip()
        if not anamnese_final:
            messagebox.showwarning("Hinweis", "Bitte zuerst Anamnese/Lückentext erstellen.")
            return

        def _do():
            einschätzung, prozedere = generate_assessment_and_plan_german(anamnese_final, status_final)
            return einschätzung, prozedere

        result = self._call_with_key_retry("Finalisierung", _do)
        if not result:
            return
        einschätzung, prozedere = result  # type: ignore[misc]

        self.fields["Einschätzung"].delete("1.0", tk.END)
        self.fields["Einschätzung"].insert(tk.END, einschätzung or "")
        self.fields["Prozedere"].delete("1.0", tk.END)
        self.fields["Prozedere"].insert(tk.END, prozedere or "")

        self.update_red_flags(anamnese_final, status_final)
        self.build_output(anamnese_final, status_final, einschätzung, prozedere)

    def on_generate_full_direct(self):
        parts: list[str] = []
        anamnese_raw = self.fields["Anamnese"].get("1.0", tk.END).strip()
        gap = self.txt_gap.get("1.0", tk.END).strip()
        anamnese_src = gap or anamnese_raw
        if anamnese_src:
            parts.append("Anamnese: " + anamnese_src)

        bef = self.fields["Status"].get("1.0", tk.END).strip()
        beu = self.fields["Einschätzung"].get("1.0", tk.END).strip()
        proz = self.fields["Prozedere"].get("1.0", tk.END).strip()
        if bef:
            parts.append("Status: " + bef)
        if beu:
            parts.append("Einschätzung: " + beu)
        if proz:
            parts.append("Prozedere: " + proz)

        combined = "\n".join(parts).strip() or (anamnese_src or "")
        if not combined:
            messagebox.showwarning("Hinweis", "Bitte Anamnese im Tool eingeben.")
            return

        def _do():
            payload, full_block = generate_full_entries_german(combined, context={})
            return payload, full_block

        result = self._call_with_key_retry("Vollgenerierung", _do)
        if not result:
            return
        payload, full_block = result  # type: ignore[misc]

        self.fields["Anamnese"].delete("1.0", tk.END)
        self.fields["Anamnese"].insert(tk.END, (payload.get("anamnese_text") or ""))
        self.fields["Status"].delete("1.0", tk.END)
        self.fields["Status"].insert(tk.END, (payload.get("status_text") or ""))
        self.fields["Einschätzung"].delete("1.0", tk.END)
        self.fields["Einschätzung"].insert(tk.END, (payload.get("einschätzung_text") or ""))
        self.fields["Prozedere"].delete("1.0", tk.END)
        self.fields["Prozedere"].insert(tk.END, (payload.get("prozedere_text") or ""))

        rf = payload.get("red_flags", []) or []
        self.set_red_flags(rf)

        self.output_full.delete("1.0", tk.END)
        self.output_full.insert(tk.END, full_block)

    # ---------- Red Flags ----------
    def update_red_flags(self, anamnese_text: str, status_text: str):
        rf_list: list[str] = []
        if load_red_flags and check_red_flags:
            try:
                path = resolve_red_flags_path(prefer_psych=False)
                data = load_red_flags(path)
                rf_hits = check_red_flags(anamnese_text + "\n" + status_text, data, return_keywords=True) or []
                rf_list = [f"{kw} – {msg}" for (kw, msg) in rf_hits]
            except Exception:
                rf_list = []
        self.set_red_flags(rf_list)

    def set_red_flags(self, items: list[str]):
        self.txt_redflags.configure(state="normal")
        self.txt_redflags.delete("1.0", tk.END)
        if items:
            self.txt_redflags.insert(tk.END, "\n".join(f"- {x}" for x in items))
        self.txt_redflags.configure(state="disabled")

    def build_output(self, anamnese: str, status: str, einschätzung: str, prozedere: str):
        parts: list[str] = [
            anamnese or "keine Angaben", "",
            status or "keine Angaben", "",
            einschätzung or "keine Angaben", "",
            prozedere or "keine Angaben",
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
        for k in ("Anamnese", "Status", "Einschätzung", "Prozedere"):
            self.fields[k].delete("1.0", tk.END)
        self.txt_gap.delete("1.0", tk.END)
        self.output_full.delete("1.0", tk.END)
        self.set_red_flags([])


def main():
    if "--smoke-test" in sys.argv:
        sys.exit(_smoke_test())

    root = tk.Tk()

    # Key beim Start sicherstellen (Dialog erscheint nur, wenn nötig)
    if not ensure_api_key(root):
        root.destroy()
        sys.exit(1)

    app = ConsultationAssistant(root)
    root.mainloop()


if __name__ == "__main__":
    main()
