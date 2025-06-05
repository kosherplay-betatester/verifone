"""
Utility functions shared across modules:
- rand_session: generate random 16-digit SESSION_ID
- to_minor: convert float amount in major units to integer minor units (×100)
- des3_decrypt: decrypt DES3-encrypted MAC_KEY
"""

import random
from base64 import b64decode
from Crypto.Cipher import DES3

# Generate a random 16-digit numeric string for session IDs
rand_session = lambda: ''.join(random.choice('0123456789') for _ in range(16))

# Convert a float (e.g., 12.34) to string of cents (e.g., "1234")
to_minor     = lambda v: str(int(round(v*100)))

def des3_decrypt(ktk: str, mac_b64: str) -> str:
    """
    Decrypt the DES3-encrypted MAC key returned from the device.
    - ktk: 16-character key-transport-key (ASCII)
    - mac_b64: base64-encoded encrypted MAC_KEY from device
    Returns the plaintext MAC key string.
    """
    if len(ktk) != 16:
        raise ValueError("KTK must be 16 ASCII chars")
    # Expand to 24 bytes with parity bits
    key24 = DES3.adjust_key_parity(ktk.encode() + ktk.encode()[:8])
    cipher = DES3.new(key24, DES3.MODE_ECB)
    # Decrypt and strip null padding
    data = cipher.decrypt(b64decode(mac_b64))
    return data.rstrip(b'\0').decode()
