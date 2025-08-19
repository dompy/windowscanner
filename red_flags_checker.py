import json, re
from typing import Dict, List, Tuple, Union

_NEG_PAT = re.compile(r"\b(kein|keine|keinen|nicht|ohne)\s+", re.IGNORECASE)

def load_red_flags(filepath: str) -> Dict[str, List[dict]]:
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

def _negated(full_text: str, keyword: str) -> bool:
    # Negation innerhalb kurzer Distanz vor dem Keyword
    pattern = re.compile(rf"{_NEG_PAT.pattern}{re.escape(keyword)}\b", re.IGNORECASE)
    return bool(pattern.search(full_text))

def check_red_flags(anamnese: str, red_flags_data: Dict[str, List[dict]],
                    return_keywords: bool = False) -> List[Union[str, Tuple[str, str]]]:
    flags: List[Union[str, Tuple[str, str]]] = []
    seen: set = set()
    full_text = anamnese.lower()

    for category_rules in red_flags_data.values():
        for rule in category_rules:
            for keyword in rule["keywords"]:
                kw_lower = keyword.lower()
                if _negated(full_text, kw_lower):
                    continue
                if kw_lower in full_text:
                    key = (kw_lower, rule["message"])
                    if key in seen:
                        continue
                    seen.add(key)
                    flags.append((keyword, rule["message"]) if return_keywords else rule["message"])
                    break
    return flags
