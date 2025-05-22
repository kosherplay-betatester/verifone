"""
Receipt writing:
- Parses the device’s XML response for relevant tags
- Builds a Hebrew-text receipt
- Saves it under receipts/YYMMDD_HHMMSS_receipt.txt
- Notifies user via QMessageBox
"""

import os, re
from datetime import datetime
from PyQt5.QtWidgets import QMessageBox

def save_receipt(xml: str):
    """
    Extracts tags like TRANSACTION_AMOUNT, RESULT_TEXT, etc.
    Constructs a list of lines for a Hebrew receipt, including any extra
    <RECEIPT_ARR> items, writes to a timestamped .txt, and pops up an info box.
    """
    # Helper to grab <TAG>value
    tag = lambda t: (re.search(fr'<{t}>([^<]*)', xml) or ['', None])[1]

    # Build header info
    ts     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    amount = int(tag('TRANSACTION_AMOUNT') or 0) / 100
    curr   = '₪' if tag('ORIGINAL_CURRENCY') == '376' else tag('ORIGINAL_CURRENCY') or ''
    result = tag('POS_TEXT') or tag('RESULT_TEXT') or ''

    lines = [
        "קבלה – Verifone P400",
        "====================",
        f"תאריך/שעה    : {ts}",
        f"סכום         : {amount:.2f} {curr}",
        f"תוצאה        : {result}",
        "",
        "כרטיס",
        "-----",
        f"מספר מוסתר   : {tag('MASKED_PAN') or 'N/A'}",
        f"מותג / מנפיק : {tag('BRAND') or 'N/A'} / {tag('ISSUER') or 'N/A'}",
        f"תוקף         : {tag('CARD_EXPIRY') or 'N/A'}",
        "",
        "עסקה",
        "-----",
        f"סוג           : {tag('TRAN_TYPE') or ''}",
        f"מס׳ אישור     : {tag('ISSUER_AUTH_NUM') or tag('AQUIRER_AUTH_NUM') or 'N/A'}",
        f"מזהה עסקה    : {tag('TRANS_ID') or 'N/A'}",
        f"אסמכתא (RRN) : {tag('RRN') or 'N/A'}",
        ""
    ]

    # Include any additional receipt array items
    arr = re.search(r'<RECEIPT_ARR>(.*?)</RECEIPT_ARR>', xml, re.S)
    if arr:
        items = re.findall(r'<RECEIPT_ARR_ITEM>(.*?)</RECEIPT_ARR_ITEM>', arr.group(1), re.S)
        for item in items:
            title = re.search(r'<HEBREW_TITLE>([^<]*)', item)
            value = re.search(r'<VALUE>([^<]*)',      item)
            if title and value:
                lines.append(f"{title.group(1)} : {value.group(1)}")

    # Ensure receipts folder exists
    os.makedirs("receipts", exist_ok=True)

    # Filename with YYMMDD_HHMMSS
    fname = datetime.now().strftime("receipts/%y%m%d_%H%M%S_receipt.txt")
    with open(fname, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # Inform the user
    QMessageBox.information(None, "הקבלה נשמרה", f"קובץ הקבלה נוצר:\n{fname}")
