"""Start the independently deployed Mac Hindsight client with this interpreter."""

import os
from pathlib import Path
import sys


client = Path.home() / ".config" / "hindsight" / "client.py"
if not client.is_file():
    sys.exit(
        "Hindsight client is missing: ~/.config/hindsight/client.py. "
        "Deploy the existing Mac LAN client and its configuration before using this plugin."
    )

os.execv(sys.executable, [sys.executable, str(client)])
