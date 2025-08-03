# red_flags_checker.py

import json
from typing import List

# Funktion zum Laden der Red-Flag-Regeln aus einer JSON-Datei
def load_red_flags(filepath: str) -> List[dict]:
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

# Funktion zur Überprüfung der Anamnese anhand der geladenen Red-Flags
def check_red_flags(anamnese: str, red_flags_data: List[dict], return_keywords: bool = False) -> List:
    flags = []
    anamnese_lower = anamnese.lower()
    for rule in red_flags_data:
        for keyword in rule["keywords"]:
            if keyword.lower() in anamnese_lower:
                if return_keywords:
                    flags.append((keyword, rule["message"]))
                else:
                    flags.append(rule["message"])
                break  # Nur eine Meldung pro Regel
    return flags
