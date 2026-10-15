import hashlib
from pathlib import Path

import yaml

from ..domain.contracts import ApiOperation, METHODS, SourceBundle, SourceDocument, WorkflowConfig


class LocalSourceReader:
    def collect(self, config: WorkflowConfig, config_directory: Path) -> SourceBundle:
        root = (config_directory / config.root).resolve()
        documents, operations = [], []
        total = 0
        prohibited = {".git", ".venv", "node_modules", ".aws", ".codex", "__pycache__"}
        for source in config.sources:
            selected = set()
            for pattern in source.paths:
                if Path(pattern).is_absolute() or ".." in Path(pattern).parts or ":" in pattern:
                    raise ValueError("source patterns must stay within project root")
                matches = sorted(root.glob(pattern))
                if not matches:
                    raise ValueError(f"source pattern matches no files: {pattern}")
                selected.update(path.resolve() for path in matches if path.is_file())
            if not selected:
                raise ValueError(f"source has no readable files: {source.id}")
            for path in sorted(selected):
                if not path.is_relative_to(root):
                    raise ValueError("source symlink escapes project root")
                relative = path.relative_to(root)
                if prohibited.intersection(relative.parts) or path.name.startswith(".env") or path.suffix in {".pem", ".key"}:
                    raise ValueError(f"credential or excluded source path: {relative.as_posix()}")
                size = path.stat().st_size
                total += size
                if total > config.max_source_bytes or len(documents) >= 100:
                    raise ValueError("source budget exceeded; select smaller source paths")
                raw = path.read_bytes()
                text = raw.decode("utf-8-sig")
                document = SourceDocument(id=f"{source.id}:{relative.as_posix()}", kind=source.kind,
                                          path=relative.as_posix(), content=text,
                                          sha256=hashlib.sha256(raw).hexdigest())
                documents.append(document)
                if source.kind == "openapi":
                    spec = yaml.safe_load(text)
                    if not isinstance(spec, dict) or "openapi" not in spec:
                        raise ValueError(f"not an OpenAPI document: {relative}")
                    for api_path, definition in spec.get("paths", {}).items():
                        if not isinstance(definition, dict):
                            continue
                        for method in definition:
                            if method.lower() in METHODS:
                                operations.append(ApiOperation(id=f"{method.upper()} {api_path}",
                                                               method=method.upper(), path=api_path,
                                                               source_id=document.id))
        ids = [doc.id for doc in documents]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate source documents")
        operation_ids = [op.id for op in operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("conflicting API operation definitions; choose a canonical OpenAPI source")
        return SourceBundle(documents=documents, operations=operations)
