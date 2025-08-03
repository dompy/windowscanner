import os
from openai import OpenAI
from red_flags_checker import check_red_flags, load_red_flags

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise EnvironmentError("❌ Umgebungsvariable OPENAI_API_KEY ist nicht gesetzt!")

client = OpenAI(api_key=api_key)

PROMPT_PREFIX = """
Beziehe dich auf anerkannte medizinische Guidelines (z. B. smarter medicine, SSGIM, EBM, Hausarztmedizin Schweiz).
Antworte immer nur mit Stichworten, keinen Sätzen, und so, wie es ein erfahrener Hausarzt in der Schweiz unter Berücksichtigung lokaler Standards tun würde. 
Falls möglich, gib am Ende vertrauenswürdige Quellen oder medizinische Guidelines an (z. B. SSGIM, smarter medicine, EBM, BAG, NICE, UpToDate).
""".strip()

red_flags_data = load_red_flags("red_flags.json")

def ask_openai(prompt: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )
    return response.choices[0].message.content.strip()

def generate_follow_up_questions(anamnese: str) -> str:
    prompt = f"""{PROMPT_PREFIX}\n\nEin Patient stellt sich mit folgender Anamnese vor:\n{anamnese}\n
Welche 5 wichtigen Symptome sind aus ärztlicher Sicht unabdingbar zu erfragen? Welche anamnestischen Befunde wären in der Hausarztprais besonders relevant, um die Diagnose einzugrenzen oder Schweregrad einzuschätzen?"""
    return ask_openai(prompt)

def generate_relevant_findings(anamnese: str) -> str:
    prompt = f"""{PROMPT_PREFIX}\n\nEin Patient stellt sich mit folgender Anamnese vor:\n{anamnese}\n
Welche klinischen Befunde (körperlich, Labor etc., jedoch ohne Anamnese) wären in der Hausarztprais besonders relevant, um die Diagnose einzugrenzen oder Schweregrad einzuschätzen?"""
    return ask_openai(prompt)

def generate_differential_diagnoses(anamnese: str, befunde: str) -> str:
    prompt = f"""{PROMPT_PREFIX}\n\nEin Patient stellt sich mit folgender Anamnese vor:\n{anamnese}\n\nKlinische Befunde:\n{befunde}\n
Mache eine Liste mit wahrscheinlichen Differentialdiagnosen (DDs), sortiert nach Relevanz, mit jeweils einer kurzen Begründung."""
    return ask_openai(prompt)

def generate_assessment(anamnese: str, befunde: str) -> str:
    prompt = f"""{PROMPT_PREFIX}\n\nAnamnese:\n{anamnese}\n\nBefunde:\n{befunde}\n
Was ist die wahrscheinlichste Diagnose bzw. ärztliche Beurteilung?"""
    return ask_openai(prompt)

def generate_procedure(beurteilung: str, befunde: str, anamnese: str) -> str:
    red_flags = check_red_flags(anamnese, red_flags_data)
    red_flag_note = ""
    if red_flags:
        red_flag_note = "⚠️ Red Flag(s):\n" + "\n".join([f"{flag} - " for flag in red_flags]) + "\n\n"

    prompt = f"""{PROMPT_PREFIX}\n\nBeurteilung:\n{beurteilung}\n\nBefunde:\n{befunde}\n
Liste stichwortartig ein empfohlenes Prozedere auf. Was wurde mit dem Patienten abgemacht? Welche Medikation wird abgegeben? Wann ist eine Verlaufskontrolle geplant? In welchen Fällen sollte er sich vorzeitig melden? Welche weiteren Abklärungen sind sinnvoll, falls keine Besserung eintritt? Bitte strukturiert antworten."""
    procedure = ask_openai(prompt)
    return red_flag_note + procedure
