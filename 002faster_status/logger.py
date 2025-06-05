# logger.py
import logging

# — configure root “verifone” logger once —
logger = logging.getLogger("verifone")
logger.setLevel(logging.DEBUG)

# write to logs.txt (in UTF-8)
fh = logging.FileHandler("logs.txt", encoding="utf-8")
fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
fh.setFormatter(fmt)
logger.addHandler(fh)

def log_traffic(sent: str, recv: str):
    """
    Log one request/response pair.
    """
    logger.debug("→ TO DEVICE:\n%s", sent.strip())
    logger.debug("← FROM DEVICE:\n%s", recv.strip())
