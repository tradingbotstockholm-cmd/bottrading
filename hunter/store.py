from __future__ import annotations
import json, os
from pathlib import Path

def read(path: str, fallback):
    p=Path(path)
    return json.loads(p.read_text()) if p.exists() else fallback

def write(path: str, obj):
    dest=Path(path)
    dest.parent.mkdir(parents=True,exist_ok=True)
    tmp=dest.with_suffix(dest.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,indent=2,sort_keys=True))
    os.replace(tmp,dest)
