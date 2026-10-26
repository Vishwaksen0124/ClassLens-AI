from __future__ import annotations

import sys
import importlib.util
from types import ModuleType
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# The lifecycle test replaces media sampling and model evaluation. Keep those
# optional runtime packages from blocking collection in lightweight dev envs.
if importlib.util.find_spec("cv2") is None:
    sys.modules["cv2"] = ModuleType("cv2")

if importlib.util.find_spec("strands") is None:
    strands = ModuleType("strands")
    strands.Agent = object
    strands.tool = lambda function: function
    sys.modules["strands"] = strands
