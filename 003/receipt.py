"""
Receipt writing:
- Parses the device’s XML response for relevant tags
- Builds a JSON receipt
- Saves it to `receipt.json` (always overwriting)
"""

import re
import json
from datetime import datetime

def save_receipt(xml: str):
    """
    Parse the device’s XML response into JSON and save the latest
    receipt as receipt.json (overwriting any existing one).
    """
    # helper to extract the first group from <TAG>value</TAG>
    tag = lambda t: (re.search(fr'<{t}>([^<]*)', xml) or [None, None])[1] or ""

    # timestamp, amount (in major units), currency symbol or code, and result text
    ts     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    amount = int(tag('TRANSACTION_AMOUNT') or 0) / 100
    curr   = '₪' if tag('ORIGINAL_CURRENCY') == '376' else tag('ORIGINAL_CURRENCY')
    result = tag('POS_TEXT') or tag('RESULT_TEXT')

    receipt = {
        "timestamp": ts,
        "amount":     amount,
        "currency":   curr,
        "result":     result,
        "card": {
            "masked_pan": tag('MASKED_PAN'),
            "brand":      tag('BRAND'),
            "issuer":     tag('ISSUER'),
            "expiry":     tag('CARD_EXPIRY')
        },
        "transaction": {
            "type":     tag('TRAN_TYPE'),
            "auth_num": tag('ISSUER_AUTH_NUM') or tag('AQUIRER_AUTH_NUM'),
            "trans_id": tag('TRANS_ID'),
            "rrn":      tag('RRN')
        },
        "items": []
    }

    # extract any <RECEIPT_ARR_ITEM> entries
    arr_block = re.search(r'<RECEIPT_ARR>(.*?)</RECEIPT_ARR>', xml, re.S)
    if arr_block:
        for item in re.findall(r'<RECEIPT_ARR_ITEM>(.*?)</RECEIPT_ARR_ITEM>', arr_block.group(1), re.S):
            title = re.search(r'<HEBREW_TITLE>([^<]*)', item)
            value = re.search(r'<VALUE>([^<]*)',      item)
            if title and value:
                receipt["items"].append({
                    "title": title.group(1),
                    "value": value.group(1)
                })

    # write (and overwrite) receipt.json
    with open("receipt.json", "w", encoding="utf-8") as f:
        json.dump(receipt, f, ensure_ascii=False, indent=2)
