# ui_assistant.py

import tkinter as tk
from tkinter import scrolledtext
import threading
import time
from word_reader import get_word_text, get_active_word_path_via_applescript
from red_flags_checker import load_red_flags
from gpt_logic import (
    generate_follow_up_questions,
    generate_relevant_findings,
    generate_assessment,
    generate_procedure,
    generate_differential_diagnoses
)

def extract_section(text: str, header: str) -> str:
    """Extrahiert den Inhalt eines Abschnitts beginnend mit `header`, bis zur nächsten bekannten Überschrift oder dem Ende."""
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
        self.root.title("🧠 KI-Konsultationsassistent")

        self.active = False

        self.toggle_button = tk.Button(root, text="🔴 Konsultation läuft", command=self.toggle, bg="white")
        self.toggle_button.pack(pady=10)

        self.red_flags_data = load_red_flags("red_flags.json")

        self.fields = {}
        for label in ["Rückfragen", "Befunde", "Differentialdiagnosen", "Beurteilung", "Prozedere"]:
            tk.Label(root, text=label, anchor="w", font=("Arial", 10, "bold")).pack(fill="x")
            text_widget = scrolledtext.ScrolledText(root, height=6, wrap=tk.WORD, bg="#1e1e1e", fg="white",
                                                    insertbackground="white")
            text_widget.pack(fill="both", padx=5, pady=2)
            self.fields[label] = text_widget

        self.status_label = tk.Label(root, text="⚠️ Kein aktives Word-Dokument erkannt.", fg="yellow")
        self.status_label.pack(pady=5)

    def toggle(self):
        self.active = not self.active
        self.toggle_button.config(
            text="🟢 Konsultation läuft" if self.active else "🔴 Konsultation läuft",
            bg="lightgreen" if self.active else "white"
        )
        if self.active:
            threading.Thread(target=self.update_loop, daemon=True).start()

    def update_loop(self):
        last_text = ""
        while self.active:
            path = get_active_word_path_via_applescript()
            if path:
                self.status_label.config(text="")
                text = get_word_text(path)
                if text and text != last_text:
                    last_text = text
                    print("🗘 Neuer Text erkannt:", text[:100])
                    self.update_fields(text)
            else:
                self.status_label.config(text="⚠️ Kein aktives Word-Dokument erkannt.")
            time.sleep(4)

    def update_fields(self, text):
        try:
            anamnese = extract_section(text, "Anamnese")
            befunde = extract_section(text, "Befunde")
            beurteilung = extract_section(text, "Beurteilung")

            print("📌 Extrahierte Anamnese:", anamnese)
            print("📌 Extrahierte Befunde:", befunde)

            self.fields["Rückfragen"].delete("1.0", tk.END)
            self.fields["Rückfragen"].insert(tk.END, generate_follow_up_questions(anamnese))

            self.fields["Befunde"].delete("1.0", tk.END)
            self.fields["Befunde"].insert(tk.END, generate_relevant_findings(anamnese))

            self.fields["Differentialdiagnosen"].delete("1.0", tk.END)
            self.fields["Differentialdiagnosen"].insert(tk.END, generate_assessment(anamnese, befunde))

            self.fields["Beurteilung"].delete("1.0", tk.END)
            self.fields["Beurteilung"].insert(tk.END, beurteilung)

            self.fields["Prozedere"].delete("1.0", tk.END)
            self.fields["Prozedere"].insert(tk.END, generate_procedure(beurteilung, befunde, anamnese))

        except Exception as e:
            print(f"⚠️ Fehler bei der Verarbeitung: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    root.configure(bg="#2e2e2e")
    app = ConsultationAssistant(root)
    root.mainloop()
