import ast
import json
import os
import re
import shlex
import socket
from collections import defaultdict, deque
from pathlib import Path

import yaml


class SourceSelectionRequired(ValueError):
    def __init__(self, message, discovered):
        super().__init__(message)
        self.discovered = discovered


class SourceDiscovery:
    EXCLUDED = {".git", ".venv", "venv", "node_modules", ".aws", ".codex", ".agents", "__pycache__", "dist", "build", ".tda-input"}

    def discover(self, workspace: Path, selected_paths=None, max_bytes=160000):
        candidates, profiles = [], []
        selected = set(selected_paths or [])
        root = workspace.resolve()
        selected_roots = []
        for value in sorted(selected):
            relative = Path(value)
            target = workspace / relative
            if (relative.is_absolute() or ".." in relative.parts or ":" in value
                    or target.is_symlink() or not target.resolve().is_relative_to(root)
                    or self.EXCLUDED.intersection(relative.parts) or not target.exists()):
                raise SourceSelectionRequired("Đường dẫn tài liệu không hợp lệ hoặc không tồn tại: " + value, [])
            selected_roots.append((value, target.resolve()))

        def explicitly_selected(path):
            resolved = path.resolve()
            return any(resolved == target or (target.is_dir() and resolved.is_relative_to(target))
                       for _, target in selected_roots)
        paths = []
        for directory, folders, files in os.walk(workspace, followlinks=False):
            folders[:] = [folder for folder in sorted(folders) if folder not in self.EXCLUDED
                           and (Path(directory) / folder).resolve().is_relative_to(workspace.resolve())]
            paths.extend(Path(directory) / filename for filename in sorted(files))
        for path in paths:
            relative = path.relative_to(workspace)
            if not path.is_file() or self.EXCLUDED.intersection(relative.parts) or path.is_symlink() or not path.resolve().is_relative_to(workspace.resolve()):
                continue
            if path.name.startswith(".env") or path.suffix in {".pem", ".key"}:
                continue
            size = path.stat().st_size
            explicit = explicitly_selected(path)
            if size > max_bytes:
                if explicit:
                    raise SourceSelectionRequired("File được chọn vượt ngân sách đầu vào: " + relative.as_posix(), candidates)
                if path.suffix != ".py" or size > 128000:
                    continue
            name = path.name.lower()
            kind = None
            if path.suffix.lower() in {".md", ".txt", ".rst"}:
                if path.parent == workspace and name.startswith("requirements") and path.suffix == ".txt":
                    kind = "code"  # Python dependency manifests are not business requirements.
                elif explicit:
                    kind = "srs" if "srs" in name else "prd"
            elif path.suffix.lower() in {".json", ".yaml", ".yml"} and explicit:
                try:
                    document = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
                    if isinstance(document, dict) and "openapi" in document:
                        kind = "openapi"
                    elif explicit:
                        kind = "prd"
                except (ValueError, UnicodeError, yaml.YAMLError):
                    pass
            if path.suffix.lower() == ".sql" and any(term in relative.as_posix().lower() for term in ("schema", "migration")):
                kind = "db_schema"
            if path.suffix.lower() in {".py", ".js", ".ts", ".tsx", ".java", ".go", ".rs", ".cs", ".html", ".css"}:
                kind = "existing_tests" if "test" in name or "tests" in relative.parts else "code"
            if name in {'pyproject.toml','package.json','dockerfile'}:
                kind = 'code'
            if kind:
                candidates.append({"path": relative.as_posix(), "kind": kind, "bytes": size})
            if path.suffix == ".py":
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
                    for node in tree.body:
                        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                            func = node.value.func
                            constructor = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
                            if constructor in {"FastAPI", "Flask"}:
                                for target in node.targets:
                                    if isinstance(target, ast.Name):
                                        routes = [decorator.args[0].value for function in tree.body
                                                  if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
                                                  for decorator in function.decorator_list
                                                  if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                                                  and isinstance(decorator.func.value, ast.Name) and decorator.func.value.id == target.id
                                                  and decorator.func.attr in {"get", "route"} and decorator.args
                                                  and isinstance(decorator.args[0], ast.Constant) and isinstance(decorator.args[0].value, str)
                                                  and "<" not in decorator.args[0].value and "{" not in decorator.args[0].value
                                                  and not any(keyword.arg == "methods" for keyword in decorator.keywords)]
                                        profiles.append({"kind": "fastapi" if constructor == "FastAPI" else "flask",
                                                         "app": relative.with_suffix("").as_posix().replace("/", ".") + ":" + target.id,
                                                         "healthcheck_path": "/openapi.json" if constructor == "FastAPI" else
                                                             "/health" if "/health" in routes else "/" if "/" in routes else routes[0] if routes else "/",
                                                         "origin": "discovered", "evidence_path": relative.as_posix()})
                except (SyntaxError, UnicodeError):
                    pass
        chosen = [candidate for candidate in candidates if explicitly_selected(workspace / candidate["path"])]
        if any(not any((workspace / c["path"]).resolve() == target or
                       (target.is_dir() and (workspace / c["path"]).resolve().is_relative_to(target))
                       for c in chosen) for _, target in selected_roots):
            raise SourceSelectionRequired("Một số nguồn không tồn tại hoặc không được hỗ trợ; chọn lại tài liệu.", candidates)
        used = sum(item["bytes"] for item in chosen)
        if used > max_bytes or len(chosen) > 100:
            raise SourceSelectionRequired("Tài liệu yêu cầu vượt ngân sách đầu vào; chọn các nguồn liên quan trong portal.", candidates)
        # Balance backend/frontend and tests instead of exhausting the budget
        # on alphabetically early code, generated clients and HTML templates.
        groups=defaultdict(deque)
        def priority(candidate):
            path=candidate['path'].lower()
            generated=('.gen.' in path or '/client/' in path or 'email-template' in path or '/components/ui/' in path)
            manifest=Path(path).name in {'pyproject.toml','package.json','dockerfile'}
            behavior=('/src/routes/' in path or '/api/routes/' in path or '/app/core/' in path
                      or Path(path).name in {'models.py','crud.py','deps.py','main.py','main.tsx'}
                      or candidate['kind']=='existing_tests')
            return (0 if manifest else 3 if generated else 1 if behavior else 2,candidate['bytes'],path)
        for candidate in sorted(candidates,key=priority):
            tier=priority(candidate)[0]
            groups[(tier,candidate['path'].split('/')[0],candidate['kind'])].append(candidate)
        balanced=[]
        for tier in (0,1,2,3):
            queues=[queue for key,queue in groups.items() if key[0]==tier]
            while any(queues):
                for queue in queues:
                    if queue:balanced.append(queue.popleft())
        for candidate in balanced:
            if candidate["kind"] not in {"code", "existing_tests", "db_schema"}:
                continue
            if candidate in chosen or used + candidate["bytes"] > max_bytes or len(chosen) >= 100:
                continue
            chosen.append(candidate)
            used += candidate["bytes"]
        node_profile = self._node_profile(workspace)
        if node_profile:
            profiles.append(node_profile)
        profile = profiles[0] if len(profiles) == 1 else {
            "kind": "unavailable", "origin": "discovered",
            "reason": "Phát hiện nhiều ứng dụng; chưa có adapter xác định service cần kiểm thử." if profiles else
                      "Chưa tìm được cách khởi động HTTP service được hỗ trợ từ repository."}
        return {"discovered": candidates, "selected": chosen, "target_profile": profile}

    def _node_profile(self, workspace):
        """Recognize a root Express service with a direct node start script and literal port."""
        manifest = workspace / "package.json"
        if not manifest.is_file() or manifest.is_symlink() or manifest.stat().st_size > 128000:
            return None
        try:
            package = json.loads(manifest.read_text(encoding="utf-8-sig"))
            if "express" not in package.get("dependencies", {}):
                return None
            command = shlex.split(package.get("scripts", {}).get("start", ""))
            if len(command) != 2 or command[0] != "node" or Path(command[1]).suffix not in {".js", ".mjs", ".cjs"}:
                return None
            entry = workspace / command[1]
            if (entry.is_symlink() or not entry.resolve().is_relative_to(workspace.resolve())
                    or not entry.is_file() or entry.stat().st_size > 128000):
                return None
            source = entry.read_text(encoding="utf-8-sig")
            match = re.search(r"\.listen\(\s*(?:process\.env\.PORT\s*\|\|\s*)?(\d+)\s*[,)]", source)
            if not match:
                return None
            port = int(match.group(1))
            routes = re.findall(r"\.get\(\s*['\"](/[^'\":{}]*)['\"]", source)
            if not 1024 <= port <= 65535 or not routes:
                return None
            return {"kind": "node", "origin": "discovered", "command": command, "port": port,
                    "healthcheck_path": "/health" if "/health" in routes else "/" if "/" in routes else routes[0],
                    "evidence_path": command[1]}
        except (ValueError, TypeError, AttributeError, UnicodeError):
            return None


class TargetProfiles:
    def materialize(self, profile):
        if profile.get("kind") == "declared":
            return dict(profile["config"])
        if profile.get("kind") == "node":
            port = profile["port"]
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", port))
            base_url = f"http://127.0.0.1:{port}"
            return {"type": "http_service", "command": list(profile["command"]), "base_url": base_url,
                    "healthcheck": base_url + profile["healthcheck_path"], "startup_timeout_seconds": 30}
        if profile.get("kind") not in {"fastapi", "flask", "command"}:
            return None
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        if profile["kind"] == "command":
            return {"type": "http_service", "command": [item.replace("{port}", str(port)) for item in profile["command"]],
                    "base_url": base_url, "healthcheck": base_url + profile["healthcheck_path"], "startup_timeout_seconds": 30}
        if profile["kind"] == "flask":
            return {"type": "http_service", "command": ["python", "-m", "flask", "--app", profile["app"],
                    "run", "--host", "127.0.0.1", "--port", str(port)], "base_url": base_url,
                    "healthcheck": base_url + profile["healthcheck_path"], "startup_timeout_seconds": 30}
        return {"type": "http_service", "command": ["python", "-m", "uvicorn", profile["app"],
                                                        "--host", "127.0.0.1", "--port", str(port)],
                "base_url": base_url, "healthcheck": base_url + "/openapi.json", "startup_timeout_seconds": 30}
