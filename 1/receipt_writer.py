import os, re
from datetime import datetime
from PyQt5.QtWidgets import QMessageBox

def save_receipt(xml: str, parent=None):
    def tag(t):
        m = re.search(fr"<{t}>([^<]*)", xml)
        return (m and m.group(1)) or None

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    amount = int(tag('TRANSACTION_AMOUNT') or 0) / 100
    curr = '₪' if tag('ORIGINAL_CURRENCY') == '376' else tag('ORIGINAL_CURRENCY') or ''
    result = tag('POS_TEXT') or tag('RESULT_TEXT') or ''
    lines = [
        "קבלה – Verifone P400",
        "====================",
        f"תאריך/שעה    : {ts}",
        f"סכום         : {amount:.2f} {curr}",
        f"תוצאה        : {result}",
        "",
        "כרטיס", "-----",
        f"מספר מוסתר   : {tag('MASKED_PAN') or 'N/A'}",
        f"מותג / מנפיק : {tag('BRAND') or 'N/A'} / {tag('ISSUER') or 'N/A'}",
        f"תוקף         : {tag('CARD_EXPIRY') or 'N/A'}",
        "", "עסקה", "-----",
        f"סוג           : {tag('TRAN_TYPE') or ''}",
        f"מס׳ אישור     : {tag('ISSUER_AUTH_NUM') or tag('AQUIRER_AUTH_NUM') or 'N/A'}",
        f"מזהה עסקה    : {tag('TRANS_ID') or 'N/A'}",
        f"אסמכתא (RRN) : {tag('RRN') or 'N/A'}",
        ""
    ]
    if arr := re.search(r"<RECEIPT_ARR>(.*?)</RECEIPT_ARR>", xml, re.S):
        for item in re.findall(r"<RECEIPT_ARR_ITEM>(.*?)</RECEIPT_ARR_ITEM>", arr.group(1), re.S):
            t = re.search(r"<HEBREW_TITLE>([^<]*)", item)
            v = re.search(r"<VALUE>([^<]*)", item)
            if t and v:
                lines.append(f"{t.group(1)} : {v.group(1)}")

    os.makedirs("receipts", exist_ok=True)
    fname = datetime.now().strftime("receipts/%y%m%d_%H%M%S_receipt.txt")
    with open(fname, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    QMessageBox.information(parent, "הקבלה נשמרה", f"קובץ הקבלה נוצר:\n{fname}")
