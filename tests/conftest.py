"""Run Qt without a display: must happen before any QApplication is created."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
