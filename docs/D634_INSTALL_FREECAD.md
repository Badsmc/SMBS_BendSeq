# D6.3.4 FreeCAD installation and hands-on acceptance

Target branch: `d6-native-3d`. Do not install the old `2D` branch if you
need the native D6.3.4 trajectory viewer and collision backend.

## Installation on Windows (FreeCAD 1.1.x)

1. Download the GitHub branch `d6-native-3d` and unzip the checkout.
2. Place the **contents** of the checkout directly inside
   `%APPDATA%/FreeCAD/v1-1/Mod/SMBS_BendSeq-2D/` (or place the complete
   checkout as its own folder under that Mod directory). The checkout root
   now contains both `Init.py` and `InitGui.py`; those delegate to the
   canonical `freecad_smbs` workbench without duplicating it.
3. Remove or disable any previous separately installed SMBS addon if it
   exposes a stale menu. Do not delete unrelated FreeCAD workbenches.
4. Download `SMBS_demo_FCStd_17_files.zip` (the user-provided original
   reference archive) into your Downloads directory; these binary models
   are NOT committed to GitHub yet.
5. Restart FreeCAD so it loads the new Python files.

## Install and verify demo models from FreeCAD Python Console

With the checkout installed in FreeCAD's user Mod folder:

```python
from pathlib import Path
import sys, FreeCAD as App
root = Path(App.getUserAppDataDir()) / "Mod" / "SMBS_BendSeq-2D"
sys.path.insert(0, str(root))
from tools.install_demo_models import install_demo_archive
install_demo_archive()  # looks in ~/Downloads
from smbs_core.demo_paths import all_demo_parts, find_demo_part
print("Available demo FCStd:", len(all_demo_parts()))
print("L model:", find_demo_part("L-(ok).FCStd"))
```

If the installed directory has a different name, change `root`. An
alternative is `install_demo_archive(r"C:\Users\User\Downloads\SMBS_demo_FCStd_17_files.zip")`.

## Run native GUI diagnosis

```python
script = root / "tools" / "freecad_gui_d633_diagnostic.py"
globals()["__file__"] = str(script)
exec(compile(script.read_text(encoding="utf-8"), str(script), "exec"), globals())
inspect_trajectory("L-(ok).FCStd", "B01")
show_sample(25)
inspect_trajectory("Z-(ok).FCStd", "B02")
show_sample(25)
inspect_trajectory("135-(ok).FCStd", "B02")
show_sample(25)
```

Wait until each `inspect_trajectory` completes before requesting samples.
Do not save the temporary diagnostic document over original fixtures.

## Run automated native viewer-context verification

```python
regression = root / "tools" / "run_d634_gui_context_regression.py"
exec(compile(regression.read_text(encoding="utf-8"), str(regression), "exec"),
     {"__file__": str(regression), "__name__": "__main__"})
```

The report goes into the user-writable SMBS reports directory, not the
source checkout.

## Acceptance limits

GitHub Actions compiles Python, exercises pure Python contracts and native
backend stubs. It does not run an interactive FreeCAD GUI or qualify real
machine safety. Recorded native collision status is sampled, not continuous
swept-volume certification; tooling support, backgauge, clamps and frame
have further physical qualification work outstanding.

A passing FreeCAD GUI demo is a necessary integration check, **not**
evidence that every manufacturing sequence is physically executable.
