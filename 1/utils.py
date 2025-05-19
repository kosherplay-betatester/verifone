import random
from base64 import b64encode, b64decode
from Crypto.Cipher import DES3
from Crypto.Hash import SHA256

def rand_session() -> str:
    return ''.join(random.choice('0123456789') for _ in range(16))

def to_minor(value: float) -> str:
    """Convert e.g. 10.00 → '1000' (cents)."""
    return str(int(round(value * 100)))

def calc_mac(xml: str, key: str) -> str:
    """SHA256(xml+key) → base64."""
    digest = SHA256.new((xml + key).encode()).digest()
    return b64encode(digest).decode()

def des3_decrypt(ktk: str, mac_b64: str) -> str:
    """Decrypt a 3DES-ECB-padded block (MAC_KEY response)."""
    if len(ktk) != 16:
        raise ValueError("KTK must be 16 ASCII chars")
    key24 = DES3.adjust_key_parity(ktk.encode() + ktk.encode()[:8])
    raw = DES3.new(key24, DES3.MODE_ECB).decrypt(b64decode(mac_b64))
    return raw.rstrip(b'\0').decode()
