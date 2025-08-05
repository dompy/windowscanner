#gpt_logic.py

import os
from openai import OpenAI
from red_flags_checker import check_red_flags, load_red_flags

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise EnvironmentError("❌ Umgebungsvariable OPENAI_API_KEY ist nicht gesetzt!")

client = OpenAI(api_key=api_key)

PROMPT_PREFIX = """
Beziehe dich auf anerkannte medizinische Guidelines (z. B. smarter medicine, SSGIM, EBM, Hausarztmedizin Schweiz).
Antworte immer nur mit Stichworten, keinen Sätzen (ausser explizit anders erwähnt), und so, wie es ein sehr erfahrener Hausarzt in der Schweiz unter Berücksichtigung lokaler Standards tun würde.
""".strip()

def ask_openai(prompt: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )
    return response.choices[0].message.content.strip()

def generate_follow_up_questions(anamnese: str) -> str:
    prompt = f"""Welche 5 wichtigen Symptome sind aus ärztlicher Sicht unabdingbar zu ergänzen? Welche anamnestischen Befunde wären in der Hausarztprais besonders relevant, um die Diagnose einzugrenzen oder Schweregrad einzuschätzen? {PROMPT_PREFIX}\n\n
    Ein Patient stellt sich mit folgender Anamnese vor:\n{anamnese}\n
    """
    return ask_openai(prompt)

def generate_relevant_findings(anamnese: str) -> str:
    prompt = f"""Welche klinischen Befunde (körperlich, Labor etc., ohne Anamnese) sind in der Hausarztprais besonders relevant zu ergänzen, um die Diagnose einzugrenzen oder Schweregrad einzuschätzen? {PROMPT_PREFIX}\n\n
    Ein Patient stellt sich mit folgender Anamnese vor:\n{anamnese}\n
    """
    return ask_openai(prompt)

def generate_differential_diagnoses(anamnese: str, befunde: str) -> str:
    prompt = f"""{PROMPT_PREFIX}

    Ein Patient stellt sich mit folgender Anamnese vor:
    {anamnese}

    Klinische Befunde:
    {befunde}

    Mache eine Liste mit mindestens 3 Differentialdiagnosen (DDs), sortiert nach Relevanz, mit jeweils einer kurzen Begründung.
    Antwortformat: Eine Aufzählung mit Bulletpoints. Keine Einzeldiagnose, keine Fliesstexte.

    """
    return ask_openai(prompt)

def generate_assessment_from_differential(selected_dds: str, anamnese: str, befunde: str) -> str:
    prompt = f"""{PROMPT_PREFIX}

    Ein Patient stellt sich mit folgender Anamnese vor:
    {anamnese}

    Klinische Befunde:
    {befunde}

    Vom Arzt ausgewählte Differentialdiagnose(n):
    {selected_dds}

    Formuliere eine kurze, konzise ärztliche Beurteilung, warum diese Diagnose(n) aufgrund der Anamnese und Befunde am ehesten zutreffen. In ein paar ganz kurzen und präzisen Sätzen, wie in einem kurzen hausärztlichen Verlaufseintrag.
    """
    return ask_openai(prompt)



def generate_assessment(anamnese: str, befunde: str) -> str:
    prompt = f"""Was ist die wahrscheinlichste Diagnose bzw. ärztliche Beurteilung? {PROMPT_PREFIX}\n\nAnamnese:\n{anamnese}\n\nBefunde:\n{befunde}Antworte in ein paar Sätzen.\n
    """
    return ask_openai(prompt)

def generate_procedure(beurteilung: str, befunde: str, anamnese: str) -> str:
    red_flags_data = load_red_flags("red_flags.json")  
    print(type(red_flags_data)), print(red_flags_data)
    red_flags = check_red_flags(anamnese, red_flags_data, return_keywords=True)

    red_flag_note = ""
    if red_flags:
        red_flag_note = "⚠️ Red Flag(s):\n" + "\n".join([f"{keyword} {message}" for keyword, message in red_flags]) + "\n\n"
    prompt = f"""{PROMPT_PREFIX}\n\nBeurteilung:\n{beurteilung}\n\nBefunde:\n{befunde}\n
    Liste stichwortartig ein empfohlenes Prozedere auf. Was wurde mit dem Patienten abgemacht? Welche Medikation wird abgegeben? Wann ist eine Verlaufskontrolle geplant? 
    In welchen Fällen sollte er sich vorzeitig wiedervorstellen? Welche weiteren Abklärungen sind zu erwägen, falls keine Besserung eintritt? Bitte strukturiert antworten.
    """
    procedure = ask_openai(prompt)

    return red_flag_note + procedure

