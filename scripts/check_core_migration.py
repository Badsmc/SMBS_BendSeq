"""Compare every core AST with the pre-migration baseline (requires Git history).

Only module names, runtime fingerprint paths and three explicit storage/path
changes are permitted. Any other formula, constant or control-flow change fails.
"""
import ast
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "67a9482"
count = 0
for file in sorted((ROOT / "freecad/bendseq/core").rglob("*.py")):
    relative = file.relative_to(ROOT / "freecad/bendseq/core").as_posix()
    before = subprocess.check_output(
        ["git", "show", BASELINE + ":smbs_core/" + relative], cwd=ROOT, text=True)
    before = (before.replace("smbs_core/", "freecad/bendseq/core/")
              .replace("freecad_smbs/", "freecad/bendseq/")
              .replace("smbs_core", "freecad.bendseq.core")
              .replace("freecad_smbs", "freecad.bendseq"))
    if relative == "kinematics2d/tooling_25d.py":
        before = before.replace(
            'repo_root = Path(__file__).resolve().parent.parent.parent\n            library_root = repo_root / "toollibrary"',
            'from freecad.bendseq.paths import initialized_tool_library\n            library_root = initialized_tool_library()')
    if relative == "kinematics2d/backgauge_cad_3d.py":
        before = before.replace('from __future__ import annotations',
            'from __future__ import annotations\nfrom freecad.bendseq.paths import tool_library')
        before = before.replace('Path(__file__).resolve().parents[2] / "toollibrary"', 'tool_library()')
    if relative == "kinematics2d/physical_validation_25d.py":
        before = before.replace('p = Path(value).expanduser()',
            'from freecad.bendseq.user_data_migration import relocated_path\n    p = Path(relocated_path(value)).expanduser()')
    assert ast.dump(ast.parse(before)) == ast.dump(ast.parse(file.read_text(encoding="utf-8"))), relative
    count += 1
print(f"{count} core modules: AST identical to baseline except imports and explicit storage path changes")

# Session serialization/decoding/restore schema stays identical; the sole
# addition is a verified historical tool-path fingerprint matcher.
before = subprocess.check_output(["git", "show", BASELINE + ":freecad_smbs/integration/fcstd_session_store.py"], cwd=ROOT, text=True)
before = before.replace("smbs_core", "freecad.bendseq.core").replace("freecad_smbs", "freecad.bendseq")
after = ast.parse((ROOT / "freecad/bendseq/integration/fcstd_session_store.py").read_text())
after.body = [node for node in after.body if not (isinstance(node, ast.FunctionDef) and node.name == "matches_tool_reference")]
assert ast.dump(ast.parse(before)) == ast.dump(after), "Session serialization changed"
print("FCStd session schema, serializer and decoder: AST identical to baseline")
