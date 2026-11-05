PLACEHOLDER_VALUES = {
    "",
    "unknown",
    "null",
    "none",
    "n/a"
}

def normalize_merchant_name(name):
    if name is None:
        return None
    
    cleaned = str(name).strip()
    
    if cleaned.lower() in PLACEHOLDER_VALUES:
        return None
    
    return cleaned.casefold()


def evaluate_merchant_extraction(actual, expected):
    
    actual_name = normalize_merchant_name(
        actual.get("merchant_name")
    )

    expected_name = normalize_merchant_name(
        expected.get("merchant_name")
    )

    passed = actual_name == expected_name

    return {
        "key": "merchant_extraction_accuracy",
        "score": 1 if passed else 0,
        "actual": actual_name,
        "expected": expected_name
    }

    