# SMBS translations

The interface follows FreeCAD General → Language. System uses the Qt system locale.

Use an English identifier in Python:

```python
tr("simulation_stopped_reason", reason=reason)
```

Add the same key to both language catalogs.

`en/catalog.json`

```json
{"simulation_stopped_reason": "Simulation stopped. {reason}"}
```

`ru/catalog.json`

```json
{"simulation_stopped_reason": "Симуляция остановлена. {reason}"}
```

Preserve formatting placeholders across languages. Restart FreeCAD after editing catalogs.

The source_keys.json mapping supports existing source strings, saved diagnostics and headless core errors. New UI text should use English identifiers directly.
