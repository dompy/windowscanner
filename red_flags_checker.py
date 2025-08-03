import json
from typing import List

# Funktion zum Laden der Red-Flag-Regeln aus einer JSON-Datei
def load_red_flags(filepath: str) -> List[dict]:
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

# Funktion zur Überprüfung der Anamnese anhand der geladenen Red-Flags
def check_red_flags(anamnese: str, red_flags_data: List[dict]) -> List[str]:
    flags = []
    anamnese_lower = anamnese.lower()
    for rule in red_flags_data:
        match_count = sum(1 for keyword in rule["keywords"] if keyword.lower() in anamnese_lower)
        if match_count >= 1:  # mindestens 1 passendes Stichwort einer Regelgruppe
            flags.append(rule["message"])
    return flags
