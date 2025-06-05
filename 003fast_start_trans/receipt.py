"""
receipt.py  –  Convert Verifone-P400 XML reply into JSON

• Extracts basic fields (timestamp, amount, result, card, transaction)
• Converts <RECEIPT_ARR_ITEM> blocks into   {english_key: value}
• Always overwrites  receipt.json  beside the executable
"""

import re
import json
from datetime import datetime

# ───────────────────────────────────────────────────────────
# Mapping: <ID> ➜ English key
ID2EN = {
    "TERMINAL_NAME":       "terminal_name",
    "SHVA_TERMINAL_ID":    "terminal_id",
    "APP_VERSION":         "app_version",
    "TRAN_TIME_STAMP":     "timestamp_local",
    "TRAN_TYPE":           "transaction_type",
    "TRANSACTION_AMOUNT":  "amount_text",
    "CURRENCY":            "currency_text",
    "ENTRY_MODE":          "entry_mode",
    "AUTH_CODE":           "authorizer",
    "UID":                 "uid",
    "POS_TRAN_ID":         "voucher_number",
    "CARD_NAME":           "card_name",
    "MASKED_PAN":          "masked_pan_tail",
    "CREDIT_TERMS":        "credit_terms",
    "FIRST_INSTALLMENT":   "first_installment",
    "PAYMENTS_NUMBER":     "additional_payments",
    "INSTALLMENT_AMOUNT":  "installment_amount",
    "AID":                 "chip_app_id",
    "ATC":                 "atc",
    "PSN":                 "psn",
    "ARC":                 "arc",
    "ASHRAIT_VERSION":     "ashrait_version",
}

# Fallback: Hebrew title ➜ English key (only when <ID> missing/unknown)
HE2EN = {
    "שם מסוף":                  "terminal_name",
    "מספר מסוף":                "terminal_id",
    "גרסת תוכנה":               "app_version",
    "תאריך ושעת העסקה":         "timestamp_local",
    "סוג עסקה":                 "transaction_type",
    "סכום עסקאה":               "amount_text",
    "סכום עסקה":               "amount_text",
    "מטבע":                    "currency_text",
    "אופן ביצוע העסקה":         "entry_mode",
    "גורם מאשר":                "authorizer",
    "מספר שובר":                "voucher_number",
    "שם כרטיס":                 "card_name",
    "מספר כרטיס":               "masked_pan_tail",
    "סוג אשראי":                "credit_terms",
    "תשלום ראשון":              "first_installment",
    "ועוד תשלומים נוספים":      "additional_payments",
    "תשלום נוסף":               "installment_amount",
    "זיהוי יישום בשבב":         "chip_app_id",
    "סידורי ש":                 "atc",
    "סידורי כ":                 "psn",
}

# ───────────────────────────────────────────────────────────
def _tag(xml: str, name: str) -> str:
    """Return first text inside <name>… or empty string."""
    m = re.search(fr'<{name}>([^<]*)', xml)
    return m.group(1) if m else ""


def save_receipt(xml: str):
    """
    Parse device XML, build Python dict, write receipt.json (UTF-8, indent=2).
    """
    # Basic header
    receipt = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "amount":    int(_tag(xml, 'TRANSACTION_AMOUNT') or 0) / 100,
        "currency":  '₪' if _tag(xml, 'ORIGINAL_CURRENCY') == '376'
                     else _tag(xml, 'ORIGINAL_CURRENCY'),
        "result":    _tag(xml, 'POS_TEXT') or _tag(xml, 'RESULT_TEXT'),
        "card": {
            "masked_pan": _tag(xml, 'MASKED_PAN'),
            "brand":      _tag(xml, 'BRAND'),
            "issuer":     _tag(xml, 'ISSUER'),
            "expiry":     _tag(xml, 'CARD_EXPIRY'),
        },
        "transaction": {
            "type":     _tag(xml, 'TRAN_TYPE'),
            "auth_num": _tag(xml, 'ISSUER_AUTH_NUM') or _tag(xml, 'AQUIRER_AUTH_NUM'),
            "trans_id": _tag(xml, 'TRANS_ID'),
            "rrn":      _tag(xml, 'RRN'),
        },
        "items": []
    }

    # Parse <RECEIPT_ARR>
    arr_block = re.search(r'<RECEIPT_ARR>(.*?)</RECEIPT_ARR>', xml, re.S)
    if arr_block:
        for raw in re.findall(r'<RECEIPT_ARR_ITEM>(.*?)</RECEIPT_ARR_ITEM>', arr_block.group(1), re.S):
            id_tag = _tag(raw, 'ID')
            title  = _tag(raw, 'HEBREW_TITLE')
            value  = _tag(raw, 'VALUE')
            key    = ID2EN.get(id_tag) or HE2EN.get(title) or id_tag or title or "unknown"
            receipt["items"].append({key: value})

    # Write to disk
    with open("receipt.json", "w", encoding="utf-8") as f:
        json.dump(receipt, f, ensure_ascii=False, indent=2)
