# ui_assistant_stepflow.py

import os
import tkinter as tk
from gpt_logic import generate_befunde_gaptext_german, generate_confirmatory_tests_for_differentials, render_diagnostics_text
from tkinter import scrolledtext, messagebox

# Wir nutzen NUR das Tool – keine Word-Integration
from gpt_logic import (
    generate_anamnese_gaptext_german,
    suggest_basic_exams_german,
    generate_assessment_and_plan_german,
    generate_full_entries_german,
)

# Red Flags separat im UI anzeigen
try:
    from red_flags_checker import load_red_flags, check_red_flags
except Exception:
    load_red_flags = None
    check_red_flags = None


class ConsultationAssistant:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("🧠 Praxis-Assistent (ohne Word)")
        self.root.geometry("1000x800")
        self.root.configure(bg="#222")

        self.fields = {}  # "Anamnese", "Befunde", "Beurteilung", "Prozedere"

        # ---------- Layout ----------
        # Anamnese (frei)
        self._label("Anamnese (frei)")
        self.fields["Anamnese"] = self._text(height=6)

        # 1) Lückentext
        self._button("1) Lückentext erzeugen (integriert)", self.on_gaptext)

        self._label("Anamnese – Lückentext (editierbar)")
        self.txt_gap = self._text(height=6)

        # 2) Untersuchungen
        toolbar = tk.Frame(self.root, bg="#222")
        toolbar.pack(fill="x", padx=8, pady=(0, 4))
        tk.Button(toolbar, text="2) Befunde (Lückentext, Basis)", command=self.on_befunde_gaptext).pack(side="left", padx=4)
        tk.Button(toolbar, text="➕ Mehr (bei Persistenz)", command=lambda: self.on_befunde_gaptext(phase="persistent")).pack(side="left", padx=4)

        self._label("Befunde (werden überschrieben)")
        self.fields["Befunde"] = self._text(height=6)

        # 3) Beurteilung + Prozedere
        self._button("3) Beurteilung + Prozedere finalisieren", self.on_finalize)

        cols = tk.Frame(self.root, bg="#222")
        cols.pack(fill="both", expand=True, padx=8)
        left = tk.Frame(cols, bg="#222"); left.pack(side="left", fill="both", expand=True, padx=(0, 4))
        right = tk.Frame(cols, bg="#222"); right.pack(side="left", fill="both", expand=True, padx=(4, 0))

        tk.Label(left, text="Beurteilung", fg="white", bg="#222", anchor="w").pack(fill="x")
        self.fields["Beurteilung"] = self._text(parent=left, height=8)

        tk.Label(right, text="Prozedere", fg="white", bg="#222", anchor="w").pack(fill="x")
        self.fields["Prozedere"] = self._text(parent=right, height=8)

        self._label("Diagnostik (Bestätigen/Ausschliessen)")
        self.fields["Diagnostik"] = self._text(height=8)

        # Warnfeld (Red Flags)
        self._label("⚠️ Red Flags (Info, nicht in den Feldern)")
        self.txt_redflags = self._text(height=4)
        self.txt_redflags.configure(state="disabled")

        # Gesamtausgabe & Utilities
        util = tk.Frame(self.root, bg="#222")
        util.pack(fill="x", padx=8, pady=(6, 0))
        tk.Button(util, text="Alles generieren (4 Felder)", command=self.on_generate_full_direct).pack(side="left", padx=4)
        tk.Button(util, text="Gesamtausgabe kopieren", command=self.copy_output).pack(side="left", padx=4)
        tk.Button(util, text="Reset", command=self.reset_all).pack(side="left", padx=4)

        self._label("Gesamtausgabe (kopierfertig)")
        self.output_full = self._text(height=10)

    # ---------- UI helpers ----------
    def _label(self, text: str):
        tk.Label(self.root, text=text, fg="white", bg="#222", anchor="w", font=("Arial", 10, "bold")).pack(fill="x", padx=8, pady=(8, 0))

    def _text(self, height=6, parent=None):
        parent = parent or self.root
        t = scrolledtext.ScrolledText(parent, height=height, wrap=tk.WORD, bg="#111", fg="white", insertbackground="white")
        t.pack(fill="both", expand=False, padx=8, pady=(4, 0))
        return t

    def _button(self, label, cmd):
        tk.Button(self.root, text=label, command=cmd).pack(padx=8, pady=(6, 0), anchor="w")

    # ---------- Actions ----------
    def on_gaptext(self):
        raw = self.fields["Anamnese"].get("1.0", tk.END).strip()
        if not raw:
            messagebox.showwarning("Hinweis", "Bitte zuerst Anamnese (frei) eingeben.")
            return
        try:
            payload, gap = generate_anamnese_gaptext_german(raw)
        except Exception as e:
            messagebox.showerror("Fehler", f"Lückentext fehlgeschlagen:\n{e}")
            return
        self.txt_gap.delete("1.0", tk.END)
        self.txt_gap.insert(tk.END, gap)

    def on_befunde_gaptext(self, phase="initial"):
        gap = self.txt_gap.get("1.0", tk.END).strip() if hasattr(self, "txt_gap") else ""
        anamnese_for_exams = gap or (self.fields.get("Anamnese").get("1.0", tk.END).strip() if "Anamnese" in self.fields else "")
        if not anamnese_for_exams:
            from tkinter import messagebox
            messagebox.showwarning("Hinweis", "Keine Anamnese vorhanden.")
            return
        try:
            payload, bef_text = generate_befunde_gaptext_german(anamnese_for_exams, phase=phase)
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror("Fehler", f"Befunde-Lückentext fehlgeschlagen:\n{e}")
            return

        if phase == "initial":
            self.fields["Befunde"].delete("1.0", tk.END)
            self.fields["Befunde"].insert(tk.END, bef_text or "")
        else:
            current = self.fields["Befunde"].get("1.0", tk.END).strip()
            if current:
                self.fields["Befunde"].insert(tk.END, "\n\n")
            self.fields["Befunde"].insert(tk.END, bef_text or "")



    def on_basic_exams(self, phase="initial"):
        gap = self.txt_gap.get("1.0", tk.END).strip()
        anamnese_for_exams = gap or self.fields["Anamnese"].get("1.0", tk.END).strip()
        if not anamnese_for_exams:
            messagebox.showwarning("Hinweis", "Keine Anamnese vorhanden.")
            return
        try:
            bef = suggest_basic_exams_german(anamnese_for_exams, phase=phase)
        except Exception as e:
            messagebox.showerror("Fehler", f"Untersuchungen fehlgeschlagen:\n{e}")
            return
        self.fields["Befunde"].delete("1.0", tk.END)
        self.fields["Befunde"].insert(tk.END, bef)

    def on_finalize(self):
        anamnese_final = self.txt_gap.get("1.0", tk.END).strip() or self.fields["Anamnese"].get("1.0", tk.END).strip()
        befunde_final = self.fields["Befunde"].get("1.0", tk.END).strip()
        if not anamnese_final:
            messagebox.showwarning("Hinweis", "Bitte zuerst Anamnese/Lückentext erstellen.")
            return
        try:
            beurteilung, prozedere = generate_assessment_and_plan_german(anamnese_final, befunde_final)
            diag_json = generate_confirmatory_tests_for_differentials(anamnese_final, befunde_final, beurteilung)
            diag_text = render_diagnostics_text(diag_json)
            self.fields["Diagnostik"].delete("1.0", tk.END)
            self.fields["Diagnostik"].insert(tk.END, diag_text or "")            
        except Exception as e:
            messagebox.showerror("Fehler", f"Finalisierung fehlgeschlagen:\n{e}")
            return

        self.fields["Beurteilung"].delete("1.0", tk.END)
        self.fields["Beurteilung"].insert(tk.END, beurteilung or "")

        self.fields["Prozedere"].delete("1.0", tk.END)
        self.fields["Prozedere"].insert(tk.END, prozedere or "")

        # Red Flags separat nachführen
        self.update_red_flags(anamnese_final, befunde_final)

        # Gesamtausgabe setzen
        self.build_output(anamnese_final, befunde_final, beurteilung, prozedere)

    def on_generate_full_direct(self):
        """Ein Klick: 4 Felder voll generieren + Red Flags + Gesamtausgabe."""
        # Kombiniere vorhandenen Inhalt als Kontext
        parts = []
        anamnese_raw = self.fields["Anamnese"].get("1.0", tk.END).strip()
        gap = self.txt_gap.get("1.0", tk.END).strip()
        anamnese_src = gap or anamnese_raw
        if anamnese_src:
            parts.append("Anamnese: " + anamnese_src)

        bef = self.fields["Befunde"].get("1.0", tk.END).strip()
        beu = self.fields["Beurteilung"].get("1.0", tk.END).strip()
        proz = self.fields["Prozedere"].get("1.0", tk.END).strip()
        if bef: parts.append("Befunde: " + bef)
        if beu: parts.append("Beurteilung: " + beu)
        if proz: parts.append("Prozedere: " + proz)

        combined = "\n".join(parts).strip() or (anamnese_src or "")
        if not combined:
            messagebox.showwarning("Hinweis", "Bitte Anamnese im Tool eingeben.")
            return

        try:
            payload, full_block = generate_full_entries_german(combined, context={})
        except Exception as e:
            messagebox.showerror("Fehler", f"Generierung fehlgeschlagen:\n{e}")
            return

        # Felder überschreiben
        self.fields["Anamnese"].delete("1.0", tk.END)
        self.fields["Anamnese"].insert(tk.END, payload.get("anamnese_text", ""))

        self.fields["Befunde"].delete("1.0", tk.END)
        self.fields["Befunde"].insert(tk.END, payload.get("befunde_text", ""))

        self.fields["Beurteilung"].delete("1.0", tk.END)
        self.fields["Beurteilung"].insert(tk.END, payload.get("beurteilung_text", ""))

        self.fields["Prozedere"].delete("1.0", tk.END)
        self.fields["Prozedere"].insert(tk.END, payload.get("prozedere_text", ""))

        # Red Flags anzeigen (separat)
        rf = payload.get("red_flags", []) or []
        self.set_red_flags(rf)

        # Gesamtausgabe
        self.output_full.delete("1.0", tk.END)
        self.output_full.insert(tk.END, full_block)

    def update_red_flags(self, anamnese_text: str, befunde_text: str):
        """Lokal Red Flags prüfen (falls Modul vorhanden)."""
        rf_list = []
        if load_red_flags and check_red_flags:
            try:
                here = os.path.dirname(os.path.abspath(__file__))
                path = os.path.join(here, "red_flags.json")
                data = load_red_flags(path)
                rf_hits = check_red_flags(anamnese_text + "\n" + befunde_text, data, return_keywords=True) or []
                rf_list = [f"{kw} – {msg}" for (kw, msg) in rf_hits]
            except Exception:
                rf_list = []
        self.set_red_flags(rf_list)

    def set_red_flags(self, items):
        self.txt_redflags.configure(state="normal")
        self.txt_redflags.delete("1.0", tk.END)
        if items:
            self.txt_redflags.insert(tk.END, "\n".join(f"- {x}" for x in items))
        self.txt_redflags.configure(state="disabled")

    def build_output(self, anamnese: str, befunde: str, beurteilung: str, prozedere: str):
        parts = []
        parts.append("Anamnese:")
        parts.append(anamnese or "keine Angaben")
        parts.append("")
        parts.append("Befunde:")
        parts.append(befunde or "keine Angaben")
        parts.append("")
        parts.append("Beurteilung:")
        parts.append(beurteilung or "keine Angaben")
        parts.append("")
        parts.append("Prozedere:")
        parts.append(prozedere or "keine Angaben")

        self.output_full.delete("1.0", tk.END)
        self.output_full.insert(tk.END, "\n".join(parts).strip())

    def copy_output(self):
        text = self.output_full.get("1.0", tk.END)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()  # nötig auf macOS
        messagebox.showinfo("Kopiert", "Gesamtausgabe in Zwischenablage.")

    def reset_all(self):
        for k in ("Anamnese", "Befunde", "Beurteilung", "Prozedere"):
            self.fields[k].delete("1.0", tk.END)
        self.txt_gap.delete("1.0", tk.END)
        self.output_full.delete("1.0", tk.END)
        self.set_red_flags([])


def main():
    root = tk.Tk()
    app = ConsultationAssistant(root)
    root.mainloop()


if __name__ == "__main__":
    main()
