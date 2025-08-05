import json
from typing import Dict, List, Tuple, Union

# Funktion zum Laden der Red-Flag-Regeln aus einer JSON-Datei
def load_red_flags(filepath: str) -> Dict[str, List[dict]]:
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

# Funktion zur Überprüfung der Anamnese anhand der geladenen Red-Flags
def check_red_flags(anamnese: str, red_flags_data: Dict[str, List[dict]], return_keywords: bool = False) -> List[Union[str, Tuple[str, str]]]:
    print(f"🧪 Red-Flag-Daten geladen mit {len(red_flags_data)} Kategorien")

    flags = []
    full_text = anamnese.lower()

    for category_rules in red_flags_data.values():  # Dictionary mit z. B. {"Kardiopulmonal": [ {rule1}, {rule2} ]}
        for rule in category_rules:
            for keyword in rule["keywords"]:
                keyword_lower = keyword.lower()

                # Prüfe auf typische Negationen in der Anamnese
                negations = [
                    f"kein {keyword_lower}",
                    f"keine {keyword_lower}",
                    f"nicht {keyword_lower}",
                    f"ohne {keyword_lower}"
                ]
                if any(neg in full_text for neg in negations):
                    continue  # Red Flag unterdrückt

                if keyword_lower in full_text:
                    if return_keywords:
                        flags.append((keyword, rule["message"]))
                    else:
                        flags.append(rule["message"])
                    break  # Nur eine Meldung pro Regel
    return flags
