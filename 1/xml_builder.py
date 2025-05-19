# xml_builder.py

# Which transaction types are allowed by the Verifone API:
VALID_TYPES = {'01', '02', '03', '06', '30', '53', '55'}

from datetime import datetime, timezone
from base64 import b64encode
from Crypto.Hash import SHA256

class XMLBuilder:
    def __init__(self, mac_key: str, training: bool = False):
        self.mac_key = mac_key
        self.training = training

    def _timestamp(self) -> str:
        """UTC timestamp in MM.DD.YYYY HH:MM:SS UTC format."""
        return datetime.now(timezone.utc).strftime("%m.%d.%Y %H:%M:%S UTC")

    def _header(self, fg: str, cmd: str, session: str) -> str:
        """Fixed header for every TRANSACTION."""
        return (
            f"<TRANSACTION>"
              f"<FUNCTION_GROUP>{fg}</FUNCTION_GROUP>"
              f"<COMMAND>{cmd}</COMMAND>"
              f"<SESSION_ID>{session}</SESSION_ID>"
              f"<TRAINING_MODE>{int(self.training)}</TRAINING_MODE>"
              f"<TRANSACTION_TIME>{self._timestamp()}</TRANSACTION_TIME>"
        )

    def _compute_mac(self, xml: str) -> str:
        """SHA256(xml + mac_key), base64-encoded."""
        digest = SHA256.new((xml + self.mac_key).encode('utf-8')).digest()
        return b64encode(digest).decode()

    def envelope(self, fg: str, cmd: str, session: str, body: str = "") -> str:
        """
        Wrap header + body, include an empty MAC placeholder for signing,
        compute the real MAC, then return the final XML.
        """
        hdr = self._header(fg, cmd, session)
        xml_for_mac = hdr + body + "<MAC></MAC></TRANSACTION>"
        mac = self._compute_mac(xml_for_mac)
        return hdr + body + f"<MAC>{mac}</MAC></TRANSACTION>"

    def start_transaction(
        self,
        session: str,
        invoice: str = "",
        cashier_id: str = "",
        shift_id: str = "",
        business_date: str = "",
        pos_ip: str = "",
        pos_port: str = "",
        show_screen: bool = True
    ) -> str:
        parts = []
        if invoice:       parts.append(f"<INVOICE>{invoice}</INVOICE>")
        if cashier_id:    parts.append(f"<CASHIER_ID>{cashier_id}</CASHIER_ID>")
        if shift_id:      parts.append(f"<SHIFT_ID>{shift_id}</SHIFT_ID>")
        if business_date: parts.append(f"<BUSINESSDATE>{business_date}</BUSINESSDATE>")
        if pos_ip:        parts.append(f"<POS_IP>{pos_ip}</POS_IP>")
        if pos_port:      parts.append(f"<POS_PORT>{pos_port}</POS_PORT>")
        if not show_screen:
            parts.append("<SHOW_SCREEN>0</SHOW_SCREEN>")
        body = "".join(parts)
        return self.envelope("SESSION", "START_TRAN", session, body)

    def discovery(
        self,
        session: str,
        timeout: int,
        restrict_token: int,
        manual: bool,
        ctls: bool,
        allow_cancel: bool,
        unattended: bool,
        tran_type: str,
        mti: str,
        entry_mode: str,
        amount: str,
        currency: str
    ) -> str:
        td = [
            f"<RESTRICT_TOKEN>{restrict_token}</RESTRICT_TOKEN>",
            f"<MANUAL>{int(manual)}</MANUAL>",
            f"<CTLS>{int(ctls)}</CTLS>",
            f"<ALLOW_CANCEL>{int(allow_cancel)}</ALLOW_CANCEL>",
            f"<UNATTENDED>{int(unattended)}</UNATTENDED>",
            f"<TRAN_TYPE>{tran_type}</TRAN_TYPE>",
            f"<MTI>{mti}</MTI>",
            f"<ENTRY_MODE>{entry_mode}</ENTRY_MODE>",
            f"<TRANSACTION_AMOUNT>{amount}</TRANSACTION_AMOUNT>",
            f"<ORIGINAL_CURRENCY>{currency}</ORIGINAL_CURRENCY>",
        ]
        body = f"<TIMEOUT>{timeout}</TIMEOUT><TRANSACTION_DETAILS>{''.join(td)}</TRANSACTION_DETAILS>"
        return self.envelope("PAYMENT", "DISCOVERY", session, body)

    def authorize(
        self,
        session: str,
        operation: int,
        tran_type: str,
        mti: str,
        amount: str,
        currency: str,
        credit_terms: int = 0,
        payments_number: int = 0,
        batch_id: int = None
    ) -> str:
        td = [
            f"<OPERATION>{operation}</OPERATION>",
            f"<TRAN_TYPE>{tran_type}</TRAN_TYPE>",
            f"<MTI>{mti}</MTI>",
            f"<TRANSACTION_AMOUNT>{amount}</TRANSACTION_AMOUNT>",
            f"<ORIGINAL_CURRENCY>{currency}</ORIGINAL_CURRENCY>",
        ]
        if credit_terms:
            td.append(f"<CREDIT_TERMS>{credit_terms}</CREDIT_TERMS>")
        if payments_number:
            td.append(f"<PAYMENTS_NUMBER>{payments_number}</PAYMENTS_NUMBER>")
        if batch_id is not None:
            td.append(f"<BATCH_ID>{batch_id}</BATCH_ID>")
        body = "<TRANSACTION_DETAILS>" + "".join(td) + "</TRANSACTION_DETAILS>"
        return self.envelope("PAYMENT", "AUTHORIZE", session, body)

    def finish_transaction(self, session: str, show_screen: bool = True) -> str:
        body = ""
        if not show_screen:
            body = "<SHOW_SCREEN>0</SHOW_SCREEN>"
        return self.envelope("SESSION", "FINISH_TRAN", session, body)
