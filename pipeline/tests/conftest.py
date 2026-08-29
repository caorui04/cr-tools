"""pytest 路径引导：把 pipeline 根目录加入 sys.path，使 `from server import workbench_state` 可用。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
