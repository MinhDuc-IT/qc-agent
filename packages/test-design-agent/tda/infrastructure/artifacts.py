import json
import sys
from datetime import datetime, timezone
from pathlib import Path


class LocalArtifactStore:
    def __init__(self, root: Path, progress=False):
        self.root = root
        self.progress = progress
        self.root.mkdir(parents=True, exist_ok=False)

    def write(self, name, data):
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError("artifact path escapes run directory")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if name.endswith('.md') and isinstance(data,str) else json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def event(self, stage, status, details):
        if self.progress:
            print(f"[{stage}] {status}", file=sys.stderr, flush=True)
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(),
                                     "stage": stage, "status": status, "details": details},
                                    ensure_ascii=False) + "\n")
