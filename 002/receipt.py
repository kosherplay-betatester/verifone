# receipt.py

import os
import re
import json
from datetime import datetime
from PyQt5.QtWidgets import QMessageBox

def save_receipt(xml: str) -> dict:
    """
    Parse the device's XML response, build a JSON-friendly dict,
    write it out to receipts/YYMMDD_HHMMSS_receipt.json, show a pop-up,
    and return the dict for in-memory storage.
    """
    # Helper to grab the first <TAG>value</TAG>
    tag = lambda t: (re.search(fr'<{t}>([^<]*)', xml) or ['', None])[1]

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    amount = int(tag('TRANSACTION_AMOUNT') or 0) / 100.0
    curr = '₪' if tag('ORIGINAL_CURRENCY') == '376' else tag('ORIGINAL_CURRENCY') or ''
    result = tag('POS_TEXT') or tag('RESULT_TEXT') or ''

    receipt = {
        'timestamp': ts,
        'amount': amount,
        'currency': curr,
        'result': result,
        'card': {
            'masked_pan': tag('MASKED_PAN') or None,
            'brand': tag('BRAND') or None,
            'issuer': tag('ISSUER') or None,
            'expiry': tag('CARD_EXPIRY') or None,
        },
        'transaction': {
            'type': tag('TRAN_TYPE') or None,
            'issuer_auth_num': tag('ISSUER_AUTH_NUM') or tag('AQUIRER_AUTH_NUM') or None,
            'trans_id': tag('TRANS_ID') or None,
            'rrn': tag('RRN') or None,
        },
        'extra': []
    }

    # Parse any <RECEIPT_ARR>…</RECEIPT_ARR> items
    arr = re.search(r'<RECEIPT_ARR>(.*?)</RECEIPT_ARR>', xml, re.S)
    if arr:
        items = re.findall(r'<RECEIPT_ARR_ITEM>(.*?)</RECEIPT_ARR_ITEM>', arr.group(1), re.S)
        for item in items:
            title = re.search(r'<HEBREW_TITLE>([^<]*)', item)
            value = re.search(r'<VALUE>([^<]*)', item)
            if title and value:
                receipt['extra'].append({
                    'title': title.group(1),
                    'value': value.group(1)
                })

    # Ensure folder
    os.makedirs('receipts', exist_ok=True)

    # Write JSON file
    fname = datetime.now().strftime('receipts/%y%m%d_%H%M%S_receipt.json')
    with open(fname, 'w', encoding='utf-8') as f:
        json.dump(receipt, f, ensure_ascii=False, indent=2)

    # Notify user
    QMessageBox.information(None, "Receipt Saved", f"JSON receipt created:\n{fname}")
    return receipt
