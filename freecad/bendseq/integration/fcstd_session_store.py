"""Embed one versioned SMBS JSON snapshot in a native FreeCAD .FCStd document.

Uses App::PropertyFileIncluded. No direct edits to the document's transient
directory, no ZIP rewriting, no Python pickle and no GUI dependency.
Importing this module does not import FreeCAD, so validation can be unit tested.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import math
from pathlib import Path
import tempfile

from freecad.bendseq.integration.document_group import group_document_objects

SCHEMA = "org.smbs.pressbrakesim.fcstd.session"
SCHEMA_VERSION = 1
OBJECT_NAME = "SMBS_Data"
ATTACHMENT_NAME = "smbs_session.json"
MAX_PAYLOAD_BYTES = 4 * 1024 * 1024
MAX_SOURCE_BREP_BYTES = 128 * 1024 * 1024


class EmbeddedSessionError(ValueError):
    """Invalid, unsupported, oversized or stale embedded SMBS data."""


def _json_bytes(payload):
    try:
        return (json.dumps(
            payload, ensure_ascii=False, sort_keys=True, indent=2,
            allow_nan=False, separators=(",", ": "),
        ) + "\n").encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise EmbeddedSessionError("SMBS data is not valid JSON") from exc


def _sha(data):
    return sha256(data).hexdigest()


def _finite(value):
    number = float(value)
    if not math.isfinite(number):
        raise EmbeddedSessionError("Nonfinite geometry coordinate")
    return round(number, 8)


def _coords(values):
    return [_finite(x) for x in values]


def geometry_manifest(session, document, *, legacy_brep=False):
    """Fingerprint authoritative SheetMetal bends AND the source CAD BRep.

    The source solid hash captures holes and geometry changes invisible in the
    unfolded bend list. Do not use Python id(), Python hash(), or transient
    FoldState angles: those change on restart or during playback.
    """
    source = session.unfold_data
    source_name = str(source.source_object)
    object_name = str(source.unfold_object)
    source_obj = document.getObject(source_name)
    unfold_obj = document.getObject(object_name)
    if source_obj is None or unfold_obj is None:
        raise EmbeddedSessionError(
            "Source or SheetMetal Unfold object is missing from the document"
        )
    shape = getattr(source_obj, "Shape", None)
    if shape is None or not hasattr(shape, "exportBrepToString"):
        raise EmbeddedSessionError("Source object lacks serializable CAD geometry")
    brep = shape.exportBrepToString()
    if not isinstance(brep, (str, bytes)) or not brep:
        raise EmbeddedSessionError("Source CAD BRep is empty")
    brep_bytes = brep.encode("utf-8") if isinstance(brep, str) else brep
    if len(brep_bytes) > MAX_SOURCE_BREP_BYTES:
        raise EmbeddedSessionError("Source CAD BRep exceeds the safe snapshot limit")
    if not legacy_brep:
        # FreeCAD can change serialization flags and signed zero on save.
        # Round-trip through the CAD reader before fingerprinting the shape.
        import Part

        roundtrip_shape = Part.Shape()
        roundtrip_shape.importBrepFromString(brep_bytes.decode("utf-8"))
        if roundtrip_shape.isNull():
            raise EmbeddedSessionError("Source CAD BRep could not be re-read")
        brep_bytes = roundtrip_shape.exportBrepToString().encode("utf-8")

    bends = []
    for b in sorted(source.bends, key=lambda item: str(item.bend_id)):
        bends.append({
            "id": str(b.bend_id),
            "fold_angle_deg": _finite(b.fold_angle_deg),
            "radius_mm": _finite(b.radius_mm),
            "inner_radius_mm": _finite(b.inner_radius_mm),
            "thickness_mm": _finite(b.thickness_mm),
            "bend_allowance_mm": _finite(b.bend_allowance_mm),
            "length_mm": _finite(b.length_mm),
            "start_xyz": _coords(b.start_xyz),
            "end_xyz": _coords(b.end_xyz),
        })
    if not bends:
        raise EmbeddedSessionError("Source SheetMetal unfold has no bends")
    panels = {
        str(pid): [_coords(v) for v in polygon]
        for pid, polygon in sorted(
            session.panel_vertices_xy.items(), key=lambda pair: str(pair[0])
        )
    }
    manifest = {
        "source_object": source_name,
        "source_face": str(source.source_face),
        "unfold_object": object_name,
        "k_factor_standard": str(source.k_factor_standard),
        "thickness_mm": _finite(source.thickness_mm),
        "source_brep_sha256": _sha(brep_bytes),
        "bends": bends,
        "panels": panels,
    }
    return {"sha256": _sha(_json_bytes(manifest)), "data": manifest}


def tool_reference(profile, *, legacy_metrics=False):
    if profile is None:
        return None
    if not hasattr(profile, "to_dict"):
        raise EmbeddedSessionError("Selected tool does not support fingerprinting")
    data = dict(profile.to_dict())
    if not legacy_metrics:
        # Reloading the same FCStd usually changes the measured import time.
        data.pop("metrics", None)
    return {
        "id": str(profile.tool_id),
        "name": str(profile.name),
        "profile_sha256": _sha(_json_bytes(data)),
    }


def matches_tool_reference(profile, reference):
    """Accept old source-path fingerprints only for SHA-verified migrated files."""
    if tool_reference(profile) == reference or tool_reference(profile, legacy_metrics=True) == reference:
        return True
    from freecad.bendseq.user_data_migration import previous_paths
    if profile is None:
        return False
    data = dict(profile.to_dict())
    for old_path in previous_paths(data.get("source_file", "")):
        for legacy_metrics in (False, True):
            candidate = dict(data, source_file=old_path)
            if not legacy_metrics:
                candidate.pop("metrics", None)
            if {"id": str(profile.tool_id), "name": str(profile.name),
                    "profile_sha256": _sha(_json_bytes(candidate))} == reference:
                return True
    return False


def _enum_to_value(value):
    if isinstance(value, Enum):
        return value.value
    return value


def _bounded_report(report):
    """Include compact diagnostic evidence, never a per-frame JSONL dump."""
    if report is None:
        return None
    if isinstance(report, dict) and isinstance(
        report.get("manual_3d_validation"), dict
    ):
        # The old compact report only kept "status", silently discarding the
        # native 3D per-bend results. Preserve all actionable manual checking
        # evidence in the SAME PropertyFileIncluded attachment as the session.
        # Exclude redundant geometry and large experimental solver objects.
        keys = (
            "schema_version", "status", "planar_status",
            "physical_certification", "selected_sequence",
            "accepted_prefix", "first_blocker", "retract_warnings",
            "sampling", "plan_cost_selected_order_complete",
            "tooling", "model", "runtime", "manual_3d_validation",
        )
        data = {k: report[k] for k in keys if k in report}
        # Stage A/B evidence matters when native 3D and 2D disagree. Keep
        # individual step summaries, not giant sampled scene meshes.
        data["planar_steps"] = [
            {k: step[k] for k in (
                "step_number", "bend_id", "angle_deg", "evidence", "reason",
                "stage_a", "stage_b",
            ) if k in step}
            for step in report.get(
                "steps", report.get("planar_steps", ())
            )
        ]
        # Refuse unrepresentable or oversized evidence. Never claim that
        # the report was embedded if it was silently dropped.
        encoded = _json_bytes(data)
        if len(encoded) > 3 * 1024 * 1024:
            raise EmbeddedSessionError(
                "Manual verification report exceeds 3 MiB; "
                "reduce diagnostics before saving this FCStd document"
            )
        return data
    if isinstance(report, dict):
        keys = (
            "status", "overall_evidence", "accepted_prefix", "first_blocker",
            "retract_warnings", "plan_cost", "plan_cost_selected_order_complete",
        )
        data = {k: report[k] for k in keys if k in report}
    else:
        keys = (
            "status", "overall_evidence", "found_sequence", "conditional_bends",
            "stage_c_native_evaluated", "max_depth_reached", "total_bends",
            "plan_cost",
        )
        data = {
            k: _enum_to_value(getattr(report, k))
            for k in keys if hasattr(report, k)
        }
    # A report from an experimental solver may contain a custom Python
    # object. Preserve the live session snapshot while honestly labeling the
    # unavailable evidence; never pickle or silently claim a verification.
    try:
        encoded = _json_bytes(data)
    except EmbeddedSessionError:
        return {
            "note": "Report is not JSON-serializable; use the external diagnostic",
            "evidence_available": False,
        }
    # Reject rather than truncate, since partial evidence could mislead.
    if len(encoded) > 256 * 1024:
        return {
            "note": "Report too large; see external diagnostic JSON/JSONL",
            "evidence_available": False,
        }
    return data


def _validate_backgauge_settings(data, canonical):
    """Validate a portable machine stop; never trust a global QSettings value."""
    if data is None:
        return None
    from freecad.bendseq.core.kinematics2d.backgauge_2d import BackgaugeModel2D

    try:
        model = BackgaugeModel2D.from_dict(data)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise EmbeddedSessionError("Invalid FCStd backgauge: " + str(exc)) from exc
    if set(model.positions) - set(canonical):
        raise EmbeddedSessionError("FCStd backgauge has unknown bend IDs")
    return model.to_dict()


def _validate_clamp_settings(data):
    """Validate project clamps without writing to shared tooling libraries."""
    if data is None:
        return None
    from freecad.bendseq.core.geometry_backend.io.clamp_validator import (
        ClampConfig, ClampValidatorError,
    )

    try:
        if not isinstance(data, dict):
            raise ValueError("clamps must be an object")
        return ClampConfig.from_dict(data).to_dict()
    except (ClampValidatorError, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise EmbeddedSessionError("Invalid FCStd clamp settings: " + str(exc)) from exc


def build_snapshot(
    session, document, tooling, cycle_config, execution_mode,
    manual_prefix=(), manual_report=None, search_report=None,
    planar_report=None, operation_weights=None,
    retract_warning=False, orientation_by_bend=None,
    orientation_source="AUTO_SHORT_REAR", saved_sequences=(),
    manual_orientation_overrides=None,
    operation_orientation_overrides=None,
    fast_advisory_orientation_ids=(),
    backgauge_model=None, clamp_config=None,
):
    """Create the portable, bounded snapshot without mutating the document."""
    if session is None or document is None:
        raise EmbeddedSessionError("A loaded SMBS session and document are required")
    geom = geometry_manifest(session, document)
    order = [str(x) for x in session.bend_order]
    canonical = [str(x) for x in session.canonical_order]
    if len(order) != len(canonical) or set(order) != set(canonical):
        raise EmbeddedSessionError("Live bend order is not a complete permutation")
    prefix = [str(x) for x in manual_prefix]
    if len(prefix) != len(set(prefix)) or order[:len(prefix)] != prefix:
        raise EmbeddedSessionError("Manual prefix disagrees with the live order")
    orientations = orientation_by_bend or {}
    if (not isinstance(orientations, dict)
            or any(k not in order or type(v) is not bool
                   for k, v in orientations.items())):
        raise EmbeddedSessionError("Invalid manual orientation map")
    if orientation_source not in ("AUTO_SHORT_REAR", "SELECTED_2D_CANDIDATE"):
        raise EmbeddedSessionError("Invalid orientation source")
    overrides = {} if manual_orientation_overrides is None else manual_orientation_overrides
    if (not isinstance(overrides, dict)
            or any(k not in order or type(v) is not bool for k, v in overrides.items())):
        raise EmbeddedSessionError("Invalid manual orientation overrides")
    stage_steps = (
        session.controller.operation_snapshot()
        if getattr(getattr(session, "controller", None), "has_stages", False)
        else []
    )
    stage_ids = {
        step["operation_id"] for step in stage_steps
        if step["operation_id"] not in canonical
    }
    stage_orientations = (
        {} if operation_orientation_overrides is None
        else operation_orientation_overrides
    )
    if (not isinstance(stage_orientations, dict)
            or any(op_id not in stage_ids or type(value) is not bool
                   for op_id, value in stage_orientations.items())):
        raise EmbeddedSessionError("Invalid independent stage orientations")
    advisory = fast_advisory_orientation_ids
    if (not isinstance(advisory, (list, tuple, set))
            or any(
                not isinstance(op_id, str) or
                op_id not in ({*overrides, *stage_orientations})
                for op_id in advisory
            )):
        raise EmbeddedSessionError("Invalid FAST advisory orientation IDs")
    # Keep document-specific machine configuration inside the FCStd.
    # External CAD models remain library references, not copied geometry.
    backgauge = _validate_backgauge_settings(
        None if backgauge_model is None else backgauge_model.to_dict(),
        canonical,
    )
    clamps = _validate_clamp_settings(
        None if clamp_config is None else clamp_config.to_dict()
    )
    config = {
        k: _enum_to_value(v)
        for k, v in asdict(cycle_config).items()
    }
    mode = _enum_to_value(execution_mode)
    weights = {str(k): _finite(v) for k, v in (operation_weights or {}).items()}
    archived_sequences = []
    seen_sequences = set()
    for item in saved_sequences:
        if not isinstance(item, dict):
            raise EmbeddedSessionError("Invalid saved sequence entry")
        sequence = item.get("bend_ids")
        if (not isinstance(sequence, (list, tuple))
                or len(sequence) != len(canonical)
                or set(sequence) != set(canonical)
                or len(set(sequence)) != len(sequence)):
            raise EmbeddedSessionError("Saved candidate is not a full bend order")
        key = tuple(sequence)
        if key in seen_sequences:
            continue
        seen_sequences.add(key)
        directions = item.get("orientation_by_bend", {})
        if (not isinstance(directions, dict)
                or any(bid not in key or type(value) is not bool
                       for bid, value in directions.items())):
            raise EmbeddedSessionError("Invalid saved candidate orientation")
        archived_sequences.append({
            "bend_ids": list(key),
            "orientation_by_bend": dict(directions),
            "origin": str(item.get("origin", "2D_SEARCH"))[:48],
            "evidence": str(item.get("evidence", "UNVERIFIED"))[:120],
        })
        if len(archived_sequences) >= 32:
            break
    snapshot = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "saved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "geometry": geom,
        "order": {
            "bend_ids": order,
            "manual_prefix": prefix,
            "orientation_by_bend": {bid: orientations[bid]
                                    for bid in order if bid in orientations},
            "orientation_source": orientation_source,
            "manual_orientation_overrides": dict(overrides),
            "operation_orientation_overrides": dict(stage_orientations),
            "fast_advisory_orientation_ids": sorted(set(advisory)),
            "canonical_bend_ids": canonical,
            "found_sequences": archived_sequences,
            # Optional operation timeline; empty in legacy single-pass plans.
            # Saved targets are cumulative physical angles.
            "operation_steps": stage_steps,
        },
        "tooling": {
            "punch": tool_reference(getattr(tooling, "punch", None)),
            "die": tool_reference(getattr(tooling, "die", None)),
            "match_active_bend_length": bool(tooling.match_active_bend_length),
            "segment_assembly_active": getattr(tooling, "tool_assembly", None) is not None,
            "allow_end_zone_collision": bool(tooling.allow_end_zone_collision),
            "end_zone_length_mm": _finite(tooling.end_zone_length_mm),
            "backgauge": backgauge,
            "clamps": clamps,
        },
        "simulation": {
            "execution_mode": str(mode),
            "cycle_config": config,
            "operation_weights": weights,
            "retract_warning": bool(retract_warning),
        },
        "diagnostics": {
            "manual": _bounded_report(manual_report),
            "automatic": _bounded_report(search_report),
            "planar": _bounded_report(planar_report),
            "restored_verification": (
                "HISTORICAL_ONLY_RECHECK_REQUIRED"
            ),
        },
        "validation": {
            "on_restore": "UNVERIFIED_RECHECK_REQUIRED",
            "reason": "Saved reports are historical evidence, never live clearance",
        },
    }
    encoded = _json_bytes(snapshot)
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise EmbeddedSessionError("Embedded SMBS JSON exceeds 4 MiB")
    return snapshot


def decode_snapshot(raw):
    """Fail closed on unknown versions, corruption and invalid bend lists."""
    if not isinstance(raw, (bytes, bytearray)):
        raise EmbeddedSessionError("Session payload must be UTF-8 bytes")
    if not raw or len(raw) > MAX_PAYLOAD_BYTES:
        raise EmbeddedSessionError("Missing or oversized embedded session")
    def reject_nonfinite(value):
        raise EmbeddedSessionError("Nonfinite JSON literal: " + str(value))

    try:
        payload = json.loads(
            raw.decode("utf-8"), parse_constant=reject_nonfinite
        )
    except (UnicodeError, ValueError) as exc:
        raise EmbeddedSessionError("Invalid embedded JSON") from exc
    if not isinstance(payload, dict):
        raise EmbeddedSessionError("Embedded JSON must be an object")
    if payload.get("schema") != SCHEMA:
        raise EmbeddedSessionError("Unknown SMBS embedded session schema")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != SCHEMA_VERSION:
        raise EmbeddedSessionError("Unsupported SMBS session schema version")
    geometry = payload.get("geometry")
    if not isinstance(geometry, dict) or not isinstance(geometry.get("data"), dict):
        raise EmbeddedSessionError("Missing geometry manifest")
    if geometry.get("sha256") != _sha(_json_bytes(geometry["data"])):
        raise EmbeddedSessionError("Geometry manifest checksum failed")
    data = payload.get("order")
    if not isinstance(data, dict):
        raise EmbeddedSessionError("Missing bend order")
    order, canonical, prefix = (
        data.get("bend_ids"), data.get("canonical_bend_ids"),
        data.get("manual_prefix"),
    )
    if not all(isinstance(x, list) and all(
        isinstance(t, str) and t for t in x
    ) for x in (order, canonical, prefix)):
        raise EmbeddedSessionError("Invalid bend identifiers in embedded session")
    if not order or len(set(order)) != len(order) or sorted(order) != sorted(canonical):
        raise EmbeddedSessionError("Stored order is not a bend permutation")
    if len(prefix) != len(set(prefix)) or order[:len(prefix)] != prefix:
        raise EmbeddedSessionError("Stored manual prefix is inconsistent")
    operation_steps = data.get("operation_steps", [])
    if (not isinstance(operation_steps, list)
            or len(operation_steps) > 2 * len(canonical)
            or any(not isinstance(step, dict) for step in operation_steps)):
        raise EmbeddedSessionError("Invalid staged operation collection")
    if operation_steps:
        seen_ids = set()
        for step in operation_steps:
            if (set(step) != {"operation_id", "bend_id", "target_angle_deg", "kind"}
                    or not isinstance(step["operation_id"], str)
                    or step["bend_id"] not in canonical
                    or step["kind"] not in ("SINGLE", "PREBEND", "FINISH")
                    or isinstance(step["target_angle_deg"], bool)
                    or not isinstance(step["target_angle_deg"], (int, float))
                    or not math.isfinite(step["target_angle_deg"])
                    or step["operation_id"] in seen_ids):
                raise EmbeddedSessionError("Invalid staged operation entry")
            seen_ids.add(step["operation_id"])
    stage_ids = {
        step["operation_id"] for step in operation_steps
        if step["operation_id"] not in canonical
    }
    stage_orientations = data.get("operation_orientation_overrides", {})
    if (not isinstance(stage_orientations, dict)
            or any(op_id not in stage_ids or type(value) is not bool
                   for op_id, value in stage_orientations.items())):
        raise EmbeddedSessionError("Invalid stored stage orientations")
    advisory = data.get("fast_advisory_orientation_ids", [])
    manual_overrides = data.get("manual_orientation_overrides", {})
    if (not isinstance(manual_overrides, dict)
            or not isinstance(advisory, list)
            or not all(isinstance(op_id, str) for op_id in advisory)
            or len(set(advisory)) != len(advisory)
            or any(op_id not in manual_overrides and op_id not in stage_orientations
                   for op_id in advisory)):
        raise EmbeddedSessionError("Invalid stored FAST advisory orientations")
    found_sequences = data.get("found_sequences", [])
    if not isinstance(found_sequences, list) or len(found_sequences) > 32:
        raise EmbeddedSessionError("Invalid saved candidate collection")
    for item in found_sequences:
        if not isinstance(item, dict):
            raise EmbeddedSessionError("Invalid saved candidate")
        sequence = item.get("bend_ids")
        directions = item.get("orientation_by_bend", {})
        if (not isinstance(sequence, list)
                or len(sequence) != len(canonical)
                or len(set(sequence)) != len(sequence)
                or set(sequence) != set(canonical)
                or not isinstance(directions, dict)
                or any(bid not in sequence or type(value) is not bool
                       for bid, value in directions.items())):
            raise EmbeddedSessionError("Saved candidate does not match the model")
    orientations = data.get("orientation_by_bend", {})
    if (not isinstance(orientations, dict)
            or any(k not in order or type(v) is not bool
                   for k, v in orientations.items())):
        raise EmbeddedSessionError("Stored manual orientations are inconsistent")
    if data.get("orientation_source", "LEGACY_CHECKBOX") not in (
            "AUTO_SHORT_REAR", "SELECTED_2D_CANDIDATE", "LEGACY_CHECKBOX"):
        raise EmbeddedSessionError("Stored orientation source is invalid")
    overrides = data.get("manual_orientation_overrides", {})
    if (not isinstance(overrides, dict)
            or any(k not in order or type(v) is not bool for k, v in overrides.items())):
        raise EmbeddedSessionError("Stored manual orientation overrides are inconsistent")
    simulation = payload.get("simulation")
    tooling = payload.get("tooling")
    if not isinstance(simulation, dict) or not isinstance(tooling, dict):
        raise EmbeddedSessionError("Missing configuration or tooling")
    if not isinstance(payload.get("diagnostics"), dict):
        raise EmbeddedSessionError("Missing diagnostic history")
    if not isinstance(payload.get("validation"), dict):
        raise EmbeddedSessionError("Missing verification metadata")
    if not isinstance(simulation.get("cycle_config"), dict):
        raise EmbeddedSessionError("Missing cycle configuration")
    if not isinstance(simulation.get("operation_weights"), dict):
        raise EmbeddedSessionError("Invalid operation weights")
    if simulation.get("execution_mode") not in ("BEND_ONLY", "FULL_CYCLE"):
        raise EmbeddedSessionError("Invalid execution mode")
    if type(tooling.get("segment_assembly_active", False)) is not bool:
        raise EmbeddedSessionError("Invalid segmented tooling setting")
    # Prior version-1 FCStd documents omit these optional sections.
    _validate_backgauge_settings(tooling.get("backgauge"), canonical)
    _validate_clamp_settings(tooling.get("clamps"))
    for key in ("punch", "die"):
        ref = tooling.get(key)
        if ref is not None and (
            not isinstance(ref, dict) or
            not isinstance(ref.get("id"), str) or
            not isinstance(ref.get("profile_sha256"), str) or
            len(ref["profile_sha256"]) != 64
        ):
            raise EmbeddedSessionError("Invalid stored tool reference: " + key)
    return payload


def validate_restore(snapshot, session, document):
    """Return validated order and prefix, or fail before any live mutation."""
    saved_hash = snapshot["geometry"]["sha256"]
    if (saved_hash != geometry_manifest(session, document)["sha256"]
            and saved_hash != geometry_manifest(session, document, legacy_brep=True)["sha256"]):
        raise EmbeddedSessionError(
            "STALE_GEOMETRY: source model or SheetMetal bend data changed"
        )
    saved = snapshot["order"]
    current = tuple(str(x) for x in session.canonical_order)
    if set(saved["bend_ids"]) != set(current) or len(saved["bend_ids"]) != len(current):
        raise EmbeddedSessionError("Saved bends do not match the current part")
    return tuple(saved["bend_ids"]), tuple(saved["manual_prefix"])


def _ensure_data_object(document):
    obj = document.getObject(OBJECT_NAME)
    if obj is None:
        obj = document.addObject("App::FeaturePython", OBJECT_NAME)
        obj.Label = "SMBS Data (embedded JSON)"
        try:
            obj.ViewObject.Visibility = False
        except (AttributeError, RuntimeError):
            pass
    if obj.Name != OBJECT_NAME:
        raise EmbeddedSessionError("SMBS data object has an unexpected name")
    specifications = (
        ("App::PropertyFileIncluded", "SessionJSON",
         "Versioned SMBS JSON included in this FCStd document"),
        ("App::PropertyString", "PayloadSHA256",
         "Checksum of the embedded JSON bytes"),
        ("App::PropertyString", "GeometrySHA256",
         "Fingerprint of the source CAD/Bend model"),
        ("App::PropertyInteger", "SchemaVersion",
         "Version of the SMBS JSON schema"),
    )
    for expected, name, description in specifications:
        if name not in obj.PropertiesList:
            obj.addProperty(expected, name, "SMBS", description)
        if hasattr(obj, "getTypeIdOfProperty"):
            got = obj.getTypeIdOfProperty(name)
            if str(got) != expected:
                raise EmbeddedSessionError(
                    "Incompatible SMBS_Data property {}: {}".format(name, got)
                )
    return obj


def write_included_json(document, snapshot):
    """Stage the JSON via PropertyFileIncluded; does NOT call document.save().

    FreeCAD copies an assigned external file into its own document transient
    store synchronously. Never edit that transient copy directly, and never
    rewrite the ZIP archive ourselves.
    """
    encoded = _json_bytes(snapshot)
    decode_snapshot(encoded)
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise EmbeddedSessionError("SMBS session exceeds the embedded size limit")
    obj = _ensure_data_object(document)
    with tempfile.TemporaryDirectory(prefix="smbs_fcstd_") as folder:
        local_path = Path(folder) / ATTACHMENT_NAME
        local_path.write_bytes(encoded)
        obj.SessionJSON = str(local_path)
        # Once the file property is set, it must reference an existing copy
        # in FreeCAD's internal transient directory, not our temporary input.
        actual_path = Path(str(obj.SessionJSON))
        if not actual_path.is_file() or actual_path.read_bytes() != encoded:
            raise EmbeddedSessionError(
                "FreeCAD did not retain the included JSON file"
            )
    obj.PayloadSHA256 = _sha(encoded)
    obj.GeometrySHA256 = str(snapshot["geometry"]["sha256"])
    obj.SchemaVersion = SCHEMA_VERSION
    group_document_objects(document)
    return obj


def read_included_json(document):
    """Read the native included file, checking digest and schema."""
    obj = document.getObject(OBJECT_NAME)
    if obj is None:
        return None
    if "SessionJSON" not in obj.PropertiesList:
        raise EmbeddedSessionError("SMBS_Data exists but lacks included JSON")
    filename = str(obj.SessionJSON)
    if not filename:
        raise EmbeddedSessionError("SMBS included JSON path is empty")
    path = Path(filename)
    if not path.is_file() or path.stat().st_size > MAX_PAYLOAD_BYTES:
        raise EmbeddedSessionError("SMBS included JSON is missing or oversized")
    raw = path.read_bytes()
    digest = str(getattr(obj, "PayloadSHA256", ""))
    if len(digest) != 64 or _sha(raw) != digest:
        raise EmbeddedSessionError("SMBS included JSON checksum mismatch")
    payload = decode_snapshot(raw)
    if (int(getattr(obj, "SchemaVersion", -1)) != SCHEMA_VERSION or
            str(getattr(obj, "GeometrySHA256", "")) != payload["geometry"]["sha256"]):
        raise EmbeddedSessionError("SMBS embedded property metadata mismatch")
    return payload
