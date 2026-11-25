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

The English and Russian catalogs are the only translation sources. English identifiers are canonical. The compatibility lookup is generated from catalog values at runtime, so previously saved Russian diagnostics remain readable. Headless core diagnostics use English by default; the UI can translate them into the selected language.
