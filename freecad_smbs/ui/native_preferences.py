"""Typed FreeCAD parameter storage with one-way legacy QSettings migration.

This adapter deliberately retains the old value/setValue call convention while
moving the storage into FreeCAD. Only explicitly supported types are accepted.
Legacy QSettings is read on first access, never written or deleted.
"""
from __future__ import annotations

import json

ROOT = "User parameter:BaseApp/Preferences/Mod/BendSeq"
_GROUPS = {
    "debug_log": "Diagnostics",
    "mesh": "Mesh",
    "backgauge": "Backgauge",
    "operation_weights": "OperationWeights",
    "sequence_catalog": "SequenceCatalog",
    "pdf": "PDF",
}
_KEYS = {
    "enabled": "Enabled",
    "grouped_view": "GroupedView",
    "config_json": "ConfigJSON",
    "json_paths": "JSONPaths",
    "options_v2": "OptionsV2",
    "positions_per_page": "PositionsPerPage",
    "large_first": "LargeFirst",
}
_METHODS = {bool: "Bool", int: "Int", float: "Float", str: "String", list: "String"}


class NativePreferences:
    """Keep defaults and JSON values in the user's FreeCAD configuration."""

    def __init__(self, qt_core=None, param_get=None):
        if param_get is None:
            import FreeCAD as App
            param_get = App.ParamGet
        self._param_get = param_get
        self._qt_core = qt_core

    def _location(self, key):
        category, name = key.split("/", 1)
        if not name or "/" in name:
            raise ValueError("Invalid BendSeq preference key: " + key)
        return self._param_get(ROOT + "/" + _GROUPS[category]), _KEYS.get(name, name)

    def _legacy(self, key, default, kind):
        core = self._qt_core
        if core is None:
            from PySide import QtCore as core
        old = core.QSettings("SMBS", "BendSeq")
        try:
            return old.value(key, default, type=kind)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _has(group, name):
        # GetContents exposes (type, name, value) for FreeCAD parameters.
        # Use the actual stored name, not a truthiness check on the value.
        return any(entry[1] == name for entry in group.GetContents())

    def value(self, key, default=None, *, type=None):
        kind = type if type is not None else default.__class__
        method = _METHODS[kind]
        group, name = self._location(key)
        if not self._has(group, name):
            marker = "Migrated_" + name
            if not group.GetBool(marker, False):
                old = self._legacy(key, default, kind)
                if old is not None:
                    self.setValue(key, old)
                group.SetBool(marker, True)
        fallback = json.dumps(default, ensure_ascii=False) if kind is list else default
        result = getattr(group, "Get" + method)(name, fallback)
        if kind is list:
            try:
                decoded = json.loads(result)
                return decoded if isinstance(decoded, list) else default
            except (ValueError, TypeError):
                return default
        return result

    def setValue(self, key, value):
        kind = value.__class__
        method = _METHODS[kind]
        group, name = self._location(key)
        stored = json.dumps(value, ensure_ascii=False) if kind is list else value
        getattr(group, "Set" + method)(name, stored)
        group.SetBool("Migrated_" + name, True)
