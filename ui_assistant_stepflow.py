import tkinter as tk
from tkinter import scrolledtext, messagebox
import threading
import time

from word_reader import get_word_text, get_active_word_path_via_applescript
from red_flags_checker import load_red_flags
from gpt_logic import (
    generate_follow_up_questions,
    generate_relevant_findings,
    generate_assessment_from_differential,
    generate_procedure,
    generate_differential_diagnoses
)

def extract_section(text: str, header: str) -> str:
    known_headers = {"Anamnese", "Befunde", "Beurteilung", "Prozedere"}
    lines = text.splitlines()
    section = []
    recording = False
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith(header.lower()):
            recording = True
            continue
        elif recording and any(stripped.lower().startswith(h.lower()) for h in known_headers if h.lower() != header.lower()):
            break
        elif recording:
            section.append(stripped)
    return "\n".join(section).strip()

class ConsultationAssistant:
    def __init__(self, root):
        self.root = root
        self.root.title("🧠 KI-Konsultationsassistent – Schritt-für-Schritt")
        self.root.configure(bg="#2e2e2e")

        # interner Zustand
        self.active = False
        self.current_anamnese = ""
        self.current_befunde = ""

        # Kopfzeile / Steuerung
        header = tk.Frame(root, bg="#2e2e2e")
        header.pack(fill="x", pady=(8, 6))

        self.toggle_button = tk.Button(header, text="🔴 Live-Abgleich AUS", command=self.toggle, bg="white")
        self.toggle_button.pack(side="left", padx=8)

        self.refresh_button = tk.Button(header, text="🔄 Word jetzt einlesen", command=self.refresh_from_word)
        self.refresh_button.pack(side="left")

        self.status_label = tk.Label(header, text="⚠️ Kein aktives Word-Dokument erkannt.", fg="yellow", bg="#2e2e2e")
        self.status_label.pack(side="left", padx=10)

        # Schritt 1 – Anamnese bestätigen -> nur Rückfragen generieren
        step1 = tk.LabelFrame(root, text="Schritt 1 – Anamnese bestätigen ➜ nur Rückfragen generieren",
                              fg="white", bg="#2e2e2e")
        step1.pack(fill="x", padx=8, pady=6)

        self.btn_anamnese = tk.Button(step1, text="✓ Anamnese bestätigen", command=self.on_confirm_anamnese)
        self.btn_anamnese.pack(side="left", padx=8, pady=6)

        # Schritt 1b – Rückfragen beantwortet -> dann Befunde-Vorschläge generieren
        step1b = tk.LabelFrame(root, text="Schritt 1b – Rückfragen beantwortet ➜ Befunde (Vorschläge) generieren",
                               fg="white", bg="#2e2e2e")
        step1b.pack(fill="x", padx=8, pady=6)

        self.btn_followups_done = tk.Button(step1b, text="✓ Rückfragen beantwortet bestätigen", command=self.on_confirm_followups_done)
        self.btn_followups_done.pack(side="left", padx=8, pady=6)

        # Felder (Ausgaben/Notizen)
        self.fields = {}
        for label, height in [
            ("Rückfragen", 6),
            ("Befunde (Vorschläge)", 6),
            ("Differentialdiagnosen", 8),
            ("Beurteilung", 6),
            ("Prozedere", 8),
        ]:
            tk.Label(root, text=label, anchor="w", font=("Arial", 10, "bold"), bg="#2e2e2e", fg="white").pack(fill="x")
            text_widget = scrolledtext.ScrolledText(root, height=height, wrap=tk.WORD, bg="#1e1e1e", fg="white",
                                                    insertbackground="white")
            text_widget.pack(fill="both", padx=8, pady=4)
            self.fields[label] = text_widget

        # Schritt 2 – Befunde bestätigen
        step2 = tk.LabelFrame(root, text="Schritt 2 – Befunde bestätigen ➜ 3+ Differentialdiagnosen erzeugen",
                              fg="white", bg="#2e2e2e")
        step2.pack(fill="x", padx=8, pady=6)
        self.btn_befunde = tk.Button(step2, text="✓ Befunde bestätigen", command=self.on_confirm_befunde)
        self.btn_befunde.pack(side="left", padx=8, pady=6)

        # Schritt 3 – Verdachtsdiagnose bestätigen
        step3 = tk.LabelFrame(root, text="Schritt 3 – Verdachtsdiagnose bestätigen ➜ Beurteilung & Prozedere",
                              fg="white", bg="#2e2e2e")
        step3.pack(fill="x", padx=8, pady=6)

        tk.Label(step3, text="Verdachtsdiagnose:", bg="#2e2e2e", fg="white").pack(side="left", padx=(8, 4))
        self.entry_dd = tk.Entry(step3, width=50)
        self.entry_dd.pack(side="left", padx=4, pady=6, ipady=2)

        self.btn_diag = tk.Button(step3, text="✓ Diagnose bestätigen", command=self.on_confirm_diagnose)
        self.btn_diag.pack(side="left", padx=8)

        # Red-Flags laden (wird in gpt_logic.generate_procedure verwendet)
        self.red_flags_data = load_red_flags("red_flags.json")

        # Startzustand: Felder leeren
        self.clear_outputs()

    # -------------------- Hintergrund-Loop --------------------

    def toggle(self):
        self.active = not self.active
        self.toggle_button.config(
            text="🟢 Live-Abgleich EIN" if self.active else "🔴 Live-Abgleich AUS",
            bg="lightgreen" if self.active else "white"
        )
        if self.active:
            threading.Thread(target=self.update_loop, daemon=True).start()

    def update_loop(self):
        last_seen = ""
        while self.active:
            path = get_active_word_path_via_applescript()
            if path:
                text = get_word_text(path)
                if text and text != last_seen:
                    last_seen = text
                    self.status_label.config(text=f"📄 Word aktiv: {path.split('/')[-1]}", fg="lightgreen")
                    self._update_cache_from_text(text)
            else:
                self.status_label.config(text="⚠️ Kein aktives Word-Dokument erkannt.", fg="yellow")
            time.sleep(4)

    def refresh_from_word(self):
        path = get_active_word_path_via_applescript()
        if not path:
            messagebox.showwarning("Hinweis", "Kein aktives Word-Dokument erkannt.")
            return
        text = get_word_text(path)
        if not text.strip():
            messagebox.showwarning("Hinweis", "Konnte keinen Text aus Word lesen.")
            return
        self._update_cache_from_text(text)
        self.status_label.config(text=f"📄 Word aktualisiert: {path.split('/')[-1]}", fg="lightgreen")

    def _update_cache_from_text(self, text: str):
        self.current_anamnese = extract_section(text, "Anamnese")
        self.current_befunde = extract_section(text, "Befunde")
        # keine automatische Generierung – nur Cache aktualisieren

    # -------------------- Schritt-Callbacks --------------------

    def on_confirm_anamnese(self):
        """Nur Rückfragen generieren – keine Befunde-Vorschläge an diesem Punkt."""
        anamnese = self.current_anamnese.strip()
        if not anamnese:
            # einmal probieren, Word frisch einzulesen
            self.refresh_from_word()
            anamnese = self.current_anamnese.strip()
        if not anamnese:
            messagebox.showwarning("Anamnese fehlt", "Im Word-Dokument wurde kein Inhalt unter 'Anamnese' gefunden.")
            return

        try:
            # Nur Rückfragen erzeugen
            self.fields["Rückfragen"].delete("1.0", tk.END)
            self.fields["Rückfragen"].insert(tk.END, generate_follow_up_questions(anamnese))

            # nach Schritt 1 alte Inhalte zurücksetzen
            self.fields["Befunde (Vorschläge)"].delete("1.0", tk.END)
            self.fields["Differentialdiagnosen"].delete("1.0", tk.END)
            self.fields["Beurteilung"].delete("1.0", tk.END)
            self.fields["Prozedere"].delete("1.0", tk.END)
        except Exception as e:
            messagebox.showerror("Fehler", f"Fehler bei Schritt 1: {e}")

    def on_confirm_followups_done(self):
        """User hat Rückfragen beantwortet -> jetzt Befunde-Vorschläge generieren (basierend auf aktualisierter Anamnese)."""
        # Anamnese erneut aus Word holen (Antworten auf Rückfragen idealerweise dort ergänzt)
        self.refresh_from_word()
        anamnese = self.current_anamnese.strip()
        if not anamnese:
            messagebox.showwarning("Anamnese fehlt", "Bitte zuerst 'Anamnese bestätigen' und die Rückfragen in Word beantworten.")
            return

        try:
            self.fields["Befunde (Vorschläge)"].delete("1.0", tk.END)
            self.fields["Befunde (Vorschläge)"].insert(tk.END, generate_relevant_findings(anamnese))
        except Exception as e:
            messagebox.showerror("Fehler", f"Fehler bei Schritt 1b: {e}")

    def on_confirm_befunde(self):
        # Befunde erneut aus Word holen (User hat echte Befunde in Word ergänzt)
        self.refresh_from_word()
        anamnese = self.current_anamnese.strip()
        befunde = self.current_befunde.strip()

        if not anamnese:
            messagebox.showwarning("Anamnese fehlt", "Bitte erst Schritt 1 durchführen.")
            return
        if not befunde:
            # Fallback: falls noch nichts in Word steht, mit Vorschlägen weiterarbeiten
            befunde = self.fields["Befunde (Vorschläge)"].get("1.0", tk.END).strip()
        if not befunde:
            messagebox.showwarning("Befunde fehlen", "Keine Befunde gefunden. Bitte in Word ergänzen oder Vorschläge nutzen.")
            return

        try:
            dd_text = generate_differential_diagnoses(anamnese, befunde)
            self.fields["Differentialdiagnosen"].delete("1.0", tk.END)
            self.fields["Differentialdiagnosen"].insert(tk.END, dd_text)
        except Exception as e:
            messagebox.showerror("Fehler", f"Fehler bei Schritt 2: {e}")

    def on_confirm_diagnose(self):
        selected = self.entry_dd.get().strip()
        if not selected:
            messagebox.showwarning("Verdachtsdiagnose fehlt", "Bitte eine Verdachtsdiagnose eingeben.")
            return

        # noch einmal sicher Anamnese/Befunde einlesen
        self.refresh_from_word()
        anamnese = self.current_anamnese.strip()
        befunde = self.current_befunde.strip()
        if not befunde:
            befunde = self.fields["Befunde (Vorschläge)"].get("1.0", tk.END).strip()

        try:
            beurteilung_text = generate_assessment_from_differential(selected, anamnese, befunde)
            self.fields["Beurteilung"].delete("1.0", tk.END)
            self.fields["Beurteilung"].insert(tk.END, beurteilung_text)

            prozedere_text = generate_procedure(beurteilung_text, befunde, anamnese)
            self.fields["Prozedere"].delete("1.0", tk.END)
            self.fields["Prozedere"].insert(tk.END, prozedere_text)
        except Exception as e:
            messagebox.showerror("Fehler", f"Fehler bei Schritt 3: {e}")

    # -------------------- Utils --------------------

    def clear_outputs(self):
        for w in self.fields.values():
            w.delete("1.0", tk.END)

if __name__ == "__main__":
    root = tk.Tk()
    app = ConsultationAssistant(root)
    root.mainloop()
