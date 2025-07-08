import os, json

SETTINGS_PATH = "settings.txt"
MAC_PATH      = "mac.txt"

# Zeroed placeholders including webhook defaults
DEFAULT_SETTINGS = {
    "ip":            "",        # device IP
    "port":          "",        # device port
    "training":      False,
    "chain":         "00000",
    "store":         "0000",
    "lane":          "000",
    "alt":           "000000000",
    "pos_type":      "",
    "quick_amount":  0.00,
    "ktk":           "",
    "webhook_host":  "localhost",  # new
    "webhook_port":  8080          # new
}

def load_settings():
    """
    Load JSON from disk; if missing, write DEFAULT_SETTINGS.
    Also merge in any new keys (like webhook_host) on upgrades.
    """
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            s = json.load(f)
    except FileNotFoundError:
        s = DEFAULT_SETTINGS.copy()
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=4)
        return s

    # Merge any missing keys (for upgrades)
    for k, v in DEFAULT_SETTINGS.items():
        s.setdefault(k, v)

    # If file existed but lacked the new keys, save it back
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=4)
    return s

def load_mac():
    if os.path.exists(MAC_PATH):
        return open(MAC_PATH, encoding="utf-8").read().strip()
    return ""

def save_mac(mac_key: str):
    with open(MAC_PATH, "w", encoding="utf-8") as f:
        f.write(mac_key)
