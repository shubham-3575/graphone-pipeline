import logging
import sys
from pathlib import Path


def setup_logging(level: int = logging.INFO, logfile: str = "logs/pipeline.log") -> None:
    Path(logfile).parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(logfile, encoding="utf-8")):
        h.setFormatter(fmt)
        root.addHandler(h)
