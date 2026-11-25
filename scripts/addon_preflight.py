"""Static runtime dependency and startup audit; does not require FreeCAD."""
import ast
from collections import defaultdict
from pathlib import Path
import sys
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sources = [ROOT / "Init.py", ROOT / "InitGui.py"]
for folder in ("freecad_smbs", "smbs_core"):
    sources.extend(sorted((ROOT / folder).rglob("*.py")))

imports = defaultdict(set)
findings = defaultdict(list)
for path in sources:
    relative = path.relative_to(ROOT).as_posix()
    text = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(text, filename=relative)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            names = [node.module.split(".")[0]]
        else:
            names = []
        for name in names:
            if name not in sys.stdlib_module_names and name not in {"freecad_smbs", "smbs_core"}:
                imports[name].add(relative)
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name.startswith("sys.path.") or name in {"sys.path", "exec", "eval", "__import__"} or any(part in name for part in ("subprocess.", "os.system", "urlopen", "requests.", "socket.", "pickle.load", "pip.main", "import_module")):
                findings[name].append(f"{relative}:{node.lineno}")

print(f"Parsed {len(sources)} Python runtime files")
print("Non-standard imports (includes optional and host modules):")
for name, files in sorted(imports.items()):
    print(f"  {name}: {', '.join(sorted(files))}")
print("Calls requiring manual review:")
for name, locations in sorted(findings.items()):
    print(f"  {name}: {', '.join(locations)}")

ns = {"p": "https://wiki.freecad.org/Package_Metadata"}
package = ET.parse(ROOT / "package.xml").getroot()
assert package.attrib["format"] == "1"
for tag in ("name", "version", "date", "description", "maintainer", "license", "icon", "content"):
    assert package.find(f"p:{tag}", ns) is not None, f"Missing {tag}"
assert (ROOT / package.findtext("p:icon", namespaces=ns)).is_file()
assert package.findtext("p:content/p:workbench/p:classname", namespaces=ns) == "SMBSWorkbench"
dependencies = {node.text for node in package.findall(".//p:depend", ns)}
assert dependencies == {"part", "SheetMetal Workbench", "techdraw"}, dependencies
for name in findings:
    assert not name.startswith("sys.path."), f"Runtime import-path modification: {name}"
print("Manifest required fields, icon, dependencies and import-path policy: OK")
licenses = {node.text: node.get("file") for node in package.findall("p:license", ns)}
assert licenses == {"LGPL-2.1-or-later": "LICENSE-Code", "CC-BY-SA-4.0": "LICENSE-Assets"}
for filename in licenses.values():
    assert (ROOT / filename).is_file()
print("License identifiers and referenced license files: OK")
print("RELEASE GATE: confirm CAD asset provenance and redistribution permissions")

