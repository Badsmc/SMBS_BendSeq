"""Audit the completed migration commit against its original baseline.

This is a historical migration guard, not a ban on later feature development.

Only module names, runtime fingerprint paths and three explicit storage/path
changes are permitted. Any other formula, constant or control-flow change fails.
"""
import ast
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "67a9482"
MIGRATION = "7e5fa7179342400432f549f7935873bd380ba0b9"
count = 0
files = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", MIGRATION,
                                 "freecad/bendseq/core"], cwd=ROOT, text=True).splitlines()
for filename in sorted(name for name in files if name.endswith(".py")):
    relative = filename.removeprefix("freecad/bendseq/core/")
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
    assert ast.dump(ast.parse(before)) == ast.dump(ast.parse(subprocess.check_output(["git", "show", MIGRATION + ":" + filename], cwd=ROOT, text=True))), relative
    count += 1
print(f"{count} core modules: AST identical to baseline except imports and explicit storage path changes")

# Session serialization/decoding/restore schema stays identical; the sole
# addition is a verified historical tool-path fingerprint matcher.
before = subprocess.check_output(["git", "show", BASELINE + ":freecad_smbs/integration/fcstd_session_store.py"], cwd=ROOT, text=True)
before = before.replace("smbs_core", "freecad.bendseq.core").replace("freecad_smbs", "freecad.bendseq")
after = ast.parse(subprocess.check_output(["git", "show", MIGRATION + ":freecad/bendseq/integration/fcstd_session_store.py"], cwd=ROOT, text=True))
after.body = [node for node in after.body if not (isinstance(node, ast.FunctionDef) and node.name == "matches_tool_reference")]
assert ast.dump(ast.parse(before)) == ast.dump(after), "Session serialization changed"
print("FCStd session schema, serializer and decoder: AST identical to baseline")
