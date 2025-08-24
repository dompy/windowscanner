# ui_assistant_stepflow.py
"""Tkinter-UI – Psychologie-Version (Erstbericht-Stil)
- Fragt beim Start den OPENAI_API_KEY per Dialog ab (maskiert) und speichert ihn prozesslokal; unter Windows optional persistent via setx.
- In allen Actions Retry-Logik: Fehlt der Key oder ist er verloren gegangen, wird er nachgefordert und der jeweilige Call einmalig erneut ausgeführt.
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
from tkinter import font as tkfont
from psy_status_editor import open_status_editor
from gpt_logic import (
    generate_assessment_and_plan_german,   # optional (nicht zwingend genutzt)
    generate_status_gaptext_german,        # optionaler Lückentext-Assistent für Status bleibt erhalten
    generate_full_entries_german,
    resolve_red_flags_path,
    compose_erstbericht,
    explain_plan_brief,
)

try:
    from red_flags_checker import check_red_flags, load_red_flags
except Exception:
    load_red_flags = None  # type: ignore
    check_red_flags = None  # type: ignore

# ---------- Utility: API-Key sicherstellen ----------

def ensure_api_key(parent, *, force_prompt: bool = False) -> bool:
    current = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not force_prompt and current:
        return True

    new_key = simpledialog.askstring(
        "OpenAI API Key",
        "Bitte OpenAI API Key eingeben:",
        show="*",
        parent=parent
    )
    if not new_key:
        if not current:
            messagebox.showwarning("Fehlender Key", "Ohne OpenAI API Key kann die App nicht arbeiten.")
            return False
        return True

    new_key = new_key.strip()
    if not new_key:
        messagebox.showwarning("Fehlender Key", "Ohne OpenAI API Key kann die App nicht arbeiten.")
        return False

    os.environ["OPENAI_API_KEY"] = new_key
    try:
        if os.name == "nt":
            subprocess.run(["setx", "OPENAI_API_KEY", new_key], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    try:
        from gpt_logic import reset_openai_client  # lazy import
        reset_openai_client()
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
        self.root.state("zoomed")
        self.root.configure(bg="#222")

        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        self.text_font = tkfont.Font(family="Arial", size=16)

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

        menubar = tk.Menu(self.root)
        m_settings = tk.Menu(menubar, tearoff=False)
        m_settings.add_command(label="API-Key ändern …", command=self.on_change_api_key)
        menubar.add_cascade(label="Einstellungen", menu=m_settings)
        self.root.config(menu=menubar)

        self.fields: dict[str, scrolledtext.ScrolledText] = {}

        m_view = tk.Menu(menubar, tearoff=False)
        m_view.add_command(label="Schrift grösser", command=lambda: self._change_text_font(+1))
        m_view.add_command(label="Schrift kleiner", command=lambda: self._change_text_font(-1))
        m_view.add_command(label="Schrift zurücksetzen", command=lambda: self._set_text_font(12))
        menubar.add_cascade(label="Ansicht", menu=m_view)

        self.root.bind("<Control-plus>",  lambda e: self._change_text_font(+1))
        self.root.bind("<Control-KP_Add>",lambda e: self._change_text_font(+1))
        self.root.bind("<Control-minus>", lambda e: self._change_text_font(-1))
        self.root.bind("<Control-KP_Subtract>", lambda e: self._change_text_font(-1))
        self.root.bind("<Control-0>",     lambda e: self._set_text_font(12))

        # Anamnese (frei)
        self._label("Anamnese (frei, Patientenstimme erlaubt)")
        self.fields["Anamnese"] = self._text(height=6)

        # Psychostatus
        bar = tk.Frame(self.root, bg="#222")
        bar.pack(fill="x", padx=8, pady=(6, 4))
        self._button("Psychopathologischen Status eingeben", self.on_psychostatus, parent=bar)

        self._label("Psychostatus (Freitext)")
        self.fields["Status"] = self._text(height=3)

        # Einschätzung + Prozedere
        self._button("Einschätzung + Prozedere generieren", self.on_finalize)

        cols = tk.Frame(self.root, bg="#222")
        cols.pack(fill="both", expand=True, padx=8)
        left = tk.Frame(cols, bg="#222")
        left.pack(side="left", fill="both", expand=True, padx=(0, 4))
        right = tk.Frame(cols, bg="#222")
        right.pack(side="left", fill="both", expand=True, padx=(4, 0))

        self._label("Einschätzung", parent=left)
        self.fields["Einschätzung"] = self._text(parent=left, height=8, expand=True)

        self._label("Empfohlenes Prozedere", parent=right)
        self.fields["Prozedere"] = self._text(parent=right, height=8, expand=True)

        # Red Flags
        self._label("⚠️ Red Flags")
        self.txt_redflags = self._text(height=4)
        self.txt_redflags.configure(state="disabled")

        # Utilities
        util = tk.Frame(self.root, bg="#222")
        util.pack(fill="x", padx=8, pady=(6, 0))
        self._button("Alles generieren (Erstbericht)", self.on_generate_full_direct, parent=util, side="left")
        self._button("Gesamtausgabe kopieren", self.copy_output, parent=util, side="left")
        self._button("Reset", self.reset_all, parent=util, side="left")

        self._label("Gesamtausgabe (Erstbericht)")
        self.output_full = self._text(height=15, expand=True)

    # ---------- UI helpers ----------
    def _label(self, text: str, parent: Optional[tk.Misc] = None, size: int = 16):
        parent = parent or self.root
        tk.Label(
            parent, text=text, fg="white", bg="#222", anchor="w",
            font=("Arial", size, "bold")
        ).pack(fill="x", padx=8 if parent is self.root else 0, pady=(8, 0))

    def _text(self, height=6, parent: Optional[tk.Misc] = None, expand: bool = False) -> scrolledtext.ScrolledText:
        parent = parent or self.root
        t = scrolledtext.ScrolledText(
            parent, height=height, wrap=tk.WORD,
            bg="#111", fg="white", insertbackground="white",
            font=self.text_font
        )
        t.pack(fill="both", expand=expand, padx=8, pady=(4, 4))
        return t

    def _change_text_font(self, delta: int):
        size = int(self.text_font.cget("size")) + delta
        size = max(8, min(28, size))
        self.text_font.configure(size=size)

    def _set_text_font(self, size: int):
        size = max(8, min(28, int(size)))
        self.text_font.configure(size=size)

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
    def _call_with_key_retry(self, action_name: str, fn: Callable[[], Tuple[Optional[dict], Optional[str]] | Tuple[str, str] | Tuple[dict, str] | str | None]):
        try:
            return fn()
        except EnvironmentError as e:
            if "OPENAI_API_KEY" in str(e):
                if ensure_api_key(self.root):
                    try:
                        return fn()
                    except Exception as e2:
                        messagebox.showerror("Fehler", f"{action_name} fehlgeschlagen:\n{e2}")
                        return None
                else:
                    return None
            messagebox.showerror("Fehler", f"{action_name} fehlgeschlagen:\n{e}")
            return None
        except Exception as e:
            msg = str(e)
            if "invalid_api_key" in msg.lower() or "authentication" in msg.lower():
                if ensure_api_key(self.root, force_prompt=True):
                    try:
                        return fn()
                    except Exception as e2:
                        messagebox.showerror("Fehler", f"{action_name} fehlgeschlagen:\n{e2}")
                        return None
                return None
            # <<< WICHTIG: alle anderen Fehler sichtbar machen
            messagebox.showerror("Fehler", f"{action_name} fehlgeschlagen:\n{e}")
            # optional: print Stacktrace fürs Terminal/Log
            import traceback; traceback.print_exc()
            return None
        
    # ---------- Actions ----------
    def on_status_gaptext(self, phase: str = "initial"):
        anamnese_src = self.fields.get("Anamnese").get("1.0", tk.END).strip()
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

    def on_psychostatus(self):
        anam = self.fields["Anamnese"].get("1.0", tk.END).strip()
        stat = self.fields["Status"].get("1.0", tk.END).strip()
        ctx = "\n\n".join(x for x in (anam, stat) if x)

        text, _state = open_status_editor(self.root, json_path="psychopathologischer_befund.json", context_text=ctx)
        if not text:
            return
        self.fields["Status"].delete("1.0", tk.END)
        self.fields["Status"].insert(tk.END, text)

    def on_finalize(self):
        anamnese_final = self.fields["Anamnese"].get("1.0", tk.END).strip()
        status_final   = self.fields["Status"].get("1.0", tk.END).strip()
        if not anamnese_final:
            messagebox.showwarning("Hinweis", "Bitte zuerst Anamnese eingeben.")
            return

        def _do_full():
            combo = "Anamnese\n " + anamnese_final
            if status_final:
                combo += "\n\nStatus\n " + status_final
            payload, _ = generate_full_entries_german(combo, context={})
            proz_ui = explain_plan_brief(
                payload.get("prozedere_text", ""),
                anamnese=anamnese_final,
                status=status_final,
                einschaetzung=payload.get("beurteilung_text", "")
            )
            return payload, proz_ui

        res = self._call_with_key_retry("Finalisierung", _do_full)
        if not res:
            return
        payload, proz_ui = res

        self.fields["Einschätzung"].delete("1.0", tk.END)
        self.fields["Einschätzung"].insert(tk.END, payload.get("beurteilung_text") or "")

        self.fields["Prozedere"].delete("1.0", tk.END)
        self.fields["Prozedere"].insert(tk.END, proz_ui or (payload.get("prozedere_text") or ""))

        self.update_red_flags(anamnese_final, status_final)

        final_report = compose_erstbericht(payload)
        self.output_full.delete("1.0", tk.END)
        self.output_full.insert(tk.END, final_report)

    def on_generate_full_direct(self):
        parts: list[str] = []
        anamnese_src = self.fields["Anamnese"].get("1.0", tk.END).strip()
        bef = self.fields["Status"].get("1.0", tk.END).strip()
        beu = self.fields["Einschätzung"].get("1.0", tk.END).strip()
        proz = self.fields["Prozedere"].get("1.0", tk.END).strip()

        if anamnese_src:
            parts.append("Anamnese\n " + anamnese_src)
        if bef:
            parts.append("Status\n " + bef)
        if beu:
            parts.append("Einschätzung\n " + beu)
        if proz:
            parts.append("Prozedere\n " + proz)

        combined = "\n\n".join(parts).strip() or (anamnese_src or "")
        if not combined:
            messagebox.showwarning("Hinweis", "Bitte Anamnese im Tool eingeben.")
            return

        def _do():
            payload, _full_block = generate_full_entries_german(combined, context={})
            return payload, compose_erstbericht(payload)

        result = self._call_with_key_retry("Vollgenerierung", _do)
        if not result:
            return
        payload, final_report = result  # type: ignore[misc]

        self.fields["Anamnese"].delete("1.0", tk.END)
        self.fields["Anamnese"].insert(tk.END, (payload.get("anamnese_text") or ""))
        self.fields["Status"].delete("1.0", tk.END)
        self.fields["Status"].insert(tk.END, (payload.get("status_text") or ""))
        self.fields["Einschätzung"].delete("1.0", tk.END)
        self.fields["Einschätzung"].insert(tk.END, (payload.get("beurteilung_text") or ""))
        self.fields["Prozedere"].delete("1.0", tk.END)
        self.fields["Prozedere"].insert(tk.END, (payload.get("prozedere_text") or ""))

        rf = payload.get("red_flags", []) or []
        self.set_red_flags(rf)

        self.output_full.delete("1.0", tk.END)
        self.output_full.insert(tk.END, final_report)

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
        parts: list[str] = []
        if anamnese:
            parts.append("Anamnese\n " + anamnese)
        if status:
            parts.append("Status\n " + status)
        if einschätzung:
            parts.append("Einschätzung\n " + einschätzung)
        if prozedere:
            parts.append("Prozedere\n " + prozedere)
        combined = "\n\n".join(parts).strip()

        def _do():
            payload, _block = generate_full_entries_german(combined, context={})
            return compose_erstbericht(payload)

        result = self._call_with_key_retry("Erstbericht generieren", _do)
        final_report = result if isinstance(result, str) else combined

        self.output_full.delete("1.0", tk.END)
        self.output_full.insert(tk.END, final_report)

    def copy_output(self):
        text = self.output_full.get("1.0", tk.END)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        messagebox.showinfo("Kopiert", "Gesamtausgabe in Zwischenablage.")

    def reset_all(self):
        for k in ("Anamnese", "Status", "Einschätzung", "Prozedere"):
            self.fields[k].delete("1.0", tk.END)
        self.output_full.delete("1.0", tk.END)
        self.set_red_flags([])

    def on_change_api_key(self):
        if ensure_api_key(self.root, force_prompt=True):
            messagebox.showinfo("OK", "API-Key wurde aktualisiert.")


def main():
    if "--smoke-test" in sys.argv:
        sys.exit(_smoke_test())

    root = tk.Tk()
    if not ensure_api_key(root):
        root.destroy()
        sys.exit(1)

    app = ConsultationAssistant(root)
    root.mainloop()


if __name__ == "__main__":
    main()


