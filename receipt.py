#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
receipt.py  –  Convert Verifone-P400 XML reply into JSON

v2.2  (ENTRY_MODE suppressed)
-----------------------------
• Any <RECEIPT_ARR_ITEM> whose ID == "ENTRY_MODE" *or* whose Hebrew
  title == "אופן ביצוע העסקה" is ignored, so the generated receipt.json
  no longer contains an "entry_mode" field.

Previous behaviour is otherwise unchanged:
• Extracts header fields (timestamp, amount, card, etc.).
• Converts remaining <RECEIPT_ARR_ITEM> blocks into   {english_key: value}
• Always overwrites receipt.json (UTF-8, indent=2)
"""

from __future__ import annotations
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
    "ENTRY_MODE":          "entry_mode",        # still mapped, but will be skipped
    "AUTH_CODE":           "authorizer",
    "ISSUER_AUTH_NUMBER":  "issuer_auth_num",
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

# Fallback: Hebrew title ➜ English key
HE2EN = {
    "שם מסוף":                  "terminal_name",
    "מספר מסוף":                "terminal_id",
    "גרסת תוכנה":               "app_version",
    "תאריך ושעת העסקה":         "timestamp_local",
    "סוג עסקה":                 "transaction_type",
    "סכום עסקאה":               "amount_text",
    "סכום עסקה":               "amount_text",
    "מטבע":                     "currency_text",
    "אופן ביצוע העסקה":         "entry_mode",        # will be skipped
    "גורם מאשר":                "authorizer",
    "מספר אישור מנפיק":         "issuer_auth_num",
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

# Tags we want to suppress entirely
_SUPPRESS_IDS = {"ENTRY_MODE"}
_SUPPRESS_HEB = {"אופן ביצוע העסקה"}

# ───────────────────────────────────────────────────────────
def _tag(xml: str, name: str) -> str:
    """Return first text inside <name>… or empty string."""
    m = re.search(fr'<{name}>([^<]*)', xml)
    return m.group(1) if m else ""


def save_receipt(xml: str):
    """Parse device XML and write receipt.json without ENTRY_MODE."""
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
        "items": {}
    }

    # —— Parse <RECEIPT_ARR> ———————————————————————————
    arr_block = re.search(r'<RECEIPT_ARR>(.*?)</RECEIPT_ARR>', xml, re.S)
    if arr_block:
        for raw in re.findall(r'<RECEIPT_ARR_ITEM>(.*?)</RECEIPT_ARR_ITEM>', arr_block.group(1), re.S):
            id_tag = _tag(raw, 'ID')
            title  = _tag(raw, 'HEBREW_TITLE')
            value  = _tag(raw, 'VALUE')

            # Skip suppressed items
            if id_tag in _SUPPRESS_IDS or title in _SUPPRESS_HEB:
                continue

            key = ID2EN.get(id_tag) or HE2EN.get(title) or id_tag or title or "unknown"
            receipt["items"][key] = value

    with open("receipt.json", "w", encoding="utf-8") as f:
        json.dump(receipt, f, ensure_ascii=False, indent=2)
