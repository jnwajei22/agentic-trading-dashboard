def expected_confirmation_phrase(pair: str) -> str:
    normalized = pair.upper().replace("/", "")
    return f"Submit the {normalized} order."

def is_exact_confirmation(pair: str, phrase: str) -> bool:
    return phrase == expected_confirmation_phrase(pair)
