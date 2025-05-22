"""
XML templating & signing:
- strip_mac: remove any existing <MAC> block
- sign_xml: insert <MAC> after </TRANSACTION_TIME> and compute SHA256-based MAC
- template_xml: build standard <TRANSACTION> bodies (START_TRAN, DISCOVERY, AUTHORIZE)
- env_xml: wrap internal ADMIN, GENERAL, SESSION commands with timestamp & sign
"""

import re
from datetime import datetime, timezone
from base64 import b64encode
from Crypto.Hash import SHA256
from utils import rand_session

def calc_mac(xml: str, key: str) -> str:
    """
    Compute a Base64-encoded SHA256 HMAC of (xml + key).
    """
    digest = SHA256.new((xml + key).encode()).digest()
    return b64encode(digest).decode()

def strip_mac(raw: str) -> str:
    """
    Remove any existing <MAC>...</MAC> block (case-insensitive, multiline).
    """
    return re.sub(r'<MAC>.*?</MAC>', '', raw, flags=re.I|re.S)

def sign_xml(raw: str, mac_key: str) -> str:
    """
    Insert a placeholder <MAC></MAC> immediately after </TRANSACTION_TIME>,
    compute the proper MAC, and substitute it.
    Raises if </TRANSACTION_TIME> is missing.
    """
    xml_no = strip_mac(raw)
    m      = re.search(r'</TRANSACTION_TIME\s*>', xml_no, flags=re.I)
    if not m:
        raise ValueError("Missing </TRANSACTION_TIME>")
    pos    = m.end()
    # Insert placeholder
    xml_ph = xml_no[:pos] + "\n  <MAC></MAC>" + xml_no[pos:]
    mac    = calc_mac(xml_ph, mac_key)
    # Replace placeholder with real MAC
    return xml_ph.replace("<MAC></MAC>", f"<MAC>{mac}</MAC>", 1)

def template_xml(fg: str, cmd: str, session: str, training: bool, mac_key: str) -> str:
    """
    Build a stand-alone <TRANSACTION> XML for Free-Call sandbox
    (e.g. SESSION START_TRAN, PAYMENT DISCOVERY, etc.).
    Then sign it with sign_xml().
    """
    if not session:
        session = rand_session()
    ts  = datetime.now().strftime("%m.%d.%Y %H:%M:%S")
    xml = (
        f"<TRANSACTION>"
        f"<FUNCTION_GROUP>{fg}</FUNCTION_GROUP>"
        f"<COMMAND>{cmd}</COMMAND>"
        f"<SESSION_ID>{session}</SESSION_ID>"
        f"<TRAINING_MODE>{int(training)}</TRAINING_MODE>"
        f"<TRANSACTION_TIME>{ts}</TRANSACTION_TIME>"
    )

    # Append the correct body based on command type
    if cmd == 'START_TRAN':
        xml += (
            "<INVOICE>100000</INVOICE>"
            "<CASHIER_ID>1</CASHIER_ID>"
            "<SHIFT_ID>1</SHIFT_ID>"
            f"<BUSINESSDATE>{datetime.now():%Y%m%d}</BUSINESSDATE>"
        )
    elif cmd == 'DISCOVERY':
        xml += (
            "<TIMEOUT>60</TIMEOUT>"
            "<TRANSACTION_DETAILS>"
            "<RESTRICT_TOKEN>0</RESTRICT_TOKEN><MANUAL>0</MANUAL>"
            "<CTLS>1</CTLS><ALLOW_CANCEL>1</ALLOW_CANCEL><UNATTENDED>0</UNATTENDED>"
            "<TRAN_TYPE>01</TRAN_TYPE><MTI>100</MTI><ENTRY_MODE>04</ENTRY_MODE>"
            "<TRANSACTION_AMOUNT>000000001000</TRANSACTION_AMOUNT>"
            "<ORIGINAL_CURRENCY>376</ORIGINAL_CURRENCY>"
            "</TRANSACTION_DETAILS>"
        )
    elif cmd == 'AUTHORIZE':
        xml += (
            "<TRANSACTION_DETAILS>"
            "<OPERATION>04</OPERATION><TRAN_TYPE>01</TRAN_TYPE>"
            "<BATCH_ID>0</BATCH_ID><MTI>100</MTI>"
            "<TRANSACTION_AMOUNT>000000001000</TRANSACTION_AMOUNT>"
            "<ORIGINAL_CURRENCY>376</ORIGINAL_CURRENCY>"
            "<CREDIT_TERMS>1</CREDIT_TERMS><RESTRICT_TOKEN>0</RESTRICT_TOKEN>"
            "</TRANSACTION_DETAILS>"
        )

    xml += "</TRANSACTION>"
    return sign_xml(xml, mac_key)

def env_xml(fg: str, cmd: str, session: str, training: bool, mac_key: str, body: str = "") -> str:
    """
    Build an internal <TRANSACTION> envelope for ADMIN / GENERAL / SESSION commands:
    includes UTC timestamp, and appends an optional <body> before signing.
    """
    ts  = datetime.now(timezone.utc).strftime("%m.%d.%Y %H:%M:%S UTC")
    hdr = (
        f"<TRANSACTION>"
        f"<FUNCTION_GROUP>{fg}</FUNCTION_GROUP>"
        f"<COMMAND>{cmd}</COMMAND>"
        f"<SESSION_ID>{session}</SESSION_ID>"
        f"<TRAINING_MODE>{int(training)}</TRAINING_MODE>"
        f"<TRANSACTION_TIME>{ts}</TRANSACTION_TIME>"
    )
    return sign_xml(hdr + body + "</TRANSACTION>", mac_key)
