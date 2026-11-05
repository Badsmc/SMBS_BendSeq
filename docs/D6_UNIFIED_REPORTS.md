# Unified SMBS reports directory

**Default location:** `<installed SMBS_BendSeq addon>/reports/`

For example, in Windows FreeCAD 1.1:

`C:\\Users\\User\\AppData\\Roaming\\FreeCAD\\v1-1\\Mod\\SMBS_BendSeq-2D\\reports\\`

The directory is determined from the module location, never hard-coded with the Windows user name.

All the following now use this folder **by default**:

- GUI motion traces: `pressbrake_*.jsonl`, `pressbrake_*.summary.json`
- GUI search diagnostics: `pressbrake_sequence_d6.json`
- GUI diagnostics: `pressbrake_2d.log`
- D6 Mesh diagnostic: `mesh_poc_*.json`
- Native Mesh comparison: `native_d6_*.json`
- Native Part fixtures: `part_benchmark_*.json`
- Native Part performance: `part_overhead_*.json`

The per-trace directory picker was removed, so GUI motion reports and search reports no longer diverge. The "Открыть общую папку отчётов" button opens the resolved report directory.

## Permission handling

For installed addons under read-only application folders, `smbs_core.report_paths.reports_dir()` tries `<addon>/reports` then falls back to `~/pressbrake_traces`. It prints a warning showing the actual path and failure reason. It NEVER silently loses diagnostics.

Advanced explicit override: set `SMBS_REPORTS_DIR` *before starting FreeCAD* to select a shared alternative folder. An existing per-tool `SMBS_*_OUTPUT` variable can override one specific diagnostic file path; do not set these if a unified folder is desired.

Existing historical files are not automatically moved or overwritten. Move them manually from:
- `~/pressbrake_traces/`
- `%APPDATA%\\FreeCAD\\v1-1\\pressbrake_traces\\`
- `%APPDATA%\\FreeCAD\\v1-1\\pressbrake_2d.log`

## Inspect the active path inside FreeCAD

```python
from smbs_core.report_paths import reports_dir
print("SMBS reports:", reports_dir())
```

No FCL or third-party library required.
