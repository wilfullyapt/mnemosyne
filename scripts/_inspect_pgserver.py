#!/usr/bin/env python3
import pgserver
from pathlib import Path

print("file", pgserver.__file__)
print("dir", [x for x in dir(pgserver) if not x.startswith("_")])
init = Path(pgserver.__file__).read_text(encoding="utf-8")
print(init[:3000])
