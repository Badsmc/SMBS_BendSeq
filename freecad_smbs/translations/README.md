# SMBS translations

Add `"Russian source text": "English translation"` to `en/catalog.json`. The Russian source is used by default; optional overrides can be placed in `ru/catalog.json`. Keep Python `{placeholders}` identical in both values. Restart FreeCAD after editing a catalog. The add-on follows FreeCAD General → Language; System uses the Qt system locale.

UI code calls `tr(source, **values)`. For errors already formatted by the headless core, the UI uses `tr_message(error)`. Add a template such as `"Гиб {} указан несколько раз.": "Bend {} is listed more than once."` to the same catalog. The core remains independent of FreeCAD; translators for generated labels are passed into it by the UI.
