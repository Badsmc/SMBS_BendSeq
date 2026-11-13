"""Replay ONE logged real native D6 blocker as exact component OCC checks.

Runs inside FreeCAD Python. This is a read-only forensic experiment and
NEVER claims that missing component hits establish safe clearance.
It deliberately DOES NOT call full-sheet multiFuse or D6 trajectory sampling.

FreeCAD Python console:
    from tools.replay_native_component_blocker import run
    result = run(
        "~/Desktop/SMBS_debug_log(20261113-144507).json",
        bend_id="B02",
        part="~/Desktop/demo/2-1-3-4-(ok) (1).FCStd",
    )
"""
from __future__ import annotations

import json
import math
from pathlib import Path, PureWindowsPath
import os
from time import perf_counter

from smbs_core.kinematics2d.developer_debug_log import desktop_directory


class BlockerReplayError(ValueError):
    """Journal or source cannot be matched to one authoritative native pose."""


def summarize_native_journal(journal: dict, *, bend_id: str = "B02") -> dict:
    """Read-only preflight that tells a local Mac journal from reference data.

    This does NOT authorize physical replay. A completed collision with a
    sampled pose and native attribution is required by select_blocker_evidence.
    """
    events = journal.get("events", []) if isinstance(journal, dict) else []
    if not isinstance(events, list):
        events = []
    finished = []
    bend_results = []
    for item in events:
        if not isinstance(item, dict):
            continue
        kind = item.get("event")
        payload = item.get("data")
        if not isinstance(payload, dict):
            continue
        if kind == "MANUAL_NATIVE_CHECK_FINISHED":
            steps = payload.get("steps")
            matches = [
                x.get("status") for x in steps if isinstance(x, dict)
                and x.get("bend_id") == bend_id
            ] if isinstance(steps, list) else []
            finished.append({
                "status": str(payload.get("status", "UNKNOWN")),
                "bend_status": matches[-1] if matches else "NOT_FOUND",
            })
        if kind == "MANUAL_NATIVE_BEND_RESULT":
            bend = payload.get("bend")
            if isinstance(bend, dict) and bend.get("bend_id") == bend_id:
                bend_results.append(str(bend.get("status", "UNKNOWN")))
    return {
        "bend_id": bend_id,
        "events": len(events),
        "finished_runs": len(finished),
        "final_run_statuses": [x["status"] for x in finished],
        "observed_bend_statuses": [x["bend_status"] for x in finished],
        "bend_results": bend_results,
        "has_finished_target_collision": any(
            x["status"] == "COLLISION" and x["bend_status"] == "COLLISION"
            for x in finished
        ),
    }


def inspect_journal(journal_path, *, bend_id: str = "B02") -> dict:
    """Explain journal contents without FreeCAD, OCC, files modification."""
    path = Path(journal_path).expanduser().resolve(strict=True)
    data = json.loads(path.read_text(encoding="utf-8"))
    summary = summarize_native_journal(data, bend_id=bend_id)
    summary["file"] = str(path)
    summary["size_bytes"] = path.stat().st_size
    return summary


def select_blocker_evidence(journal: dict, bend_id: str = "B02") -> dict:
    """Parse one completed Manual native run without trusting arbitrary JSON.

    This pure Python part is headless-testable. The FreeCAD runner must still
    reproduce the ORIGINAL fold state and machine placement independently.
    """
    if not isinstance(journal, dict) or journal.get("schema_version") != "smbs.desktop_developer_debug.v1":
        raise BlockerReplayError("unsupported_native_developer_journal")
    events = journal.get("events")
    if not isinstance(events, list):
        raise BlockerReplayError("missing_events")
    if not isinstance(bend_id, str) or not bend_id:
        raise BlockerReplayError("invalid_bend_id")

    last = None
    for i, event in enumerate(events):
        if not isinstance(event, dict) or event.get("event") != "MANUAL_NATIVE_CHECK_FINISHED":
            continue
        final = event.get("data")
        if not isinstance(final, dict):
            continue
        steps = final.get("steps")
        if final.get("status") != "COLLISION":
            continue
        if not isinstance(steps, list):
            continue
        candidate = next((s for s in steps if isinstance(s, dict)
                          and s.get("bend_id") == bend_id
                          and s.get("status") == "COLLISION"), None)
        if candidate is None:
            continue
        sid = event.get("session_id")
        history = [
            e for e in events[:i] if isinstance(e, dict)
            and e.get("session_id") == sid
        ]
        starts = [e.get("data") for e in history
                  if e.get("event") == "MANUAL_NATIVE_CHECK_STARTED"]
        part_loads = [e.get("data") for e in history
                      if e.get("event") == "PART_LOAD_REQUEST"]
        geometries = [e.get("data") for e in history
                      if e.get("event") == "PART_GEOMETRY_LOADED"]
        if not starts or not part_loads or not geometries:
            raise BlockerReplayError("missing_matching_native_source_events")
        started, part, geometry = starts[-1], part_loads[-1], geometries[-1]
        blocker = candidate.get("first_blocker")
        if not isinstance(blocker, dict) or blocker.get("tool") not in ("PUNCH", "DIE", "PUNCH_AND_DIE"):
            raise BlockerReplayError("missing_proven_tool_blocker")
        pose = blocker.get("sampled_machine_pose")
        if not isinstance(pose, dict) or pose.get("bend_id") != bend_id:
            raise BlockerReplayError("missing_exact_sampled_machine_pose")
        if pose.get("phase") != blocker.get("phase"):
            raise BlockerReplayError("phase_mismatch")
        attr = blocker.get("component_attribution") or {}
        if not isinstance(attr, dict):
            raise BlockerReplayError("invalid_attribution")
        raw_hits = attr.get("hits") or []
        if not isinstance(raw_hits, list):
            raise BlockerReplayError("invalid_attributed_hits")
        hit_ids = []
        volumes = {}
        for h in raw_hits:
            if not isinstance(h, dict) or not isinstance(h.get("component_id"), str):
                raise BlockerReplayError("invalid_hit")
            if h.get("tool") not in ("PUNCH", "DIE"):
                raise BlockerReplayError("invalid_hit_tool")
            component_id = h["component_id"]
            canonical = component_id.removeprefix("panel:")
            if canonical in volumes:
                raise BlockerReplayError("duplicate_hit")
            vol = float(h.get("overlap_volume_mm3"))
            if not math.isfinite(vol) or vol <= 0:
                raise BlockerReplayError("invalid_hit_volume")
            hit_ids.append(canonical)
            volumes[canonical] = {"volume_mm3": vol, "tool": h["tool"]}
        if not hit_ids:
            raise BlockerReplayError("no_recorded_native_component_hits")
        last = {
            "bend_id": bend_id,
            "native_status": candidate.get("native_status"),
            "recorded_status": candidate.get("status"),
            "part_path": part.get("document_path"),
            "unfold_name": (
                (part.get("selection") or [{}])[0].get("name")
                if isinstance(part.get("selection"), list) else None
            ),
            "punch_path": started.get("punch_file"),
            "die_path": started.get("die_file"),
            "virtual_tool_length": started.get("virtual_tool_length"),
            "tool_sections": started.get("tool_sections") or {},
            "reverse_axis": (
                (final.get("reverse_axis_by_bend") or {}).get(bend_id)
            ),
            "requested_order": started.get("bend_order"),
            "geometry": geometry,
            "config": started.get("adaptive_config") or {},
            "pose": pose,
            "attributed_hits": volumes,
            "first_sampled_blocking_u": (
                (blocker.get("sampled_onset_bracket") or {})
                .get("first_sampled_blocking_u")
            ),
            "full_oracle_first_overlap_mm3": blocker.get("first_sample_punch_overlap_mm3"),
        }
    if last is None:
        summary = summarize_native_journal(journal, bend_id=bend_id)
        raise BlockerReplayError(
            "no_completed_native_collision_for_requested_bend:"
            + str(bend_id)
            + "; finished_runs=" + str(summary["finished_runs"])
            + "; recorded_bend_results=" + str(summary["bend_results"])
            + "; final_run_statuses=" + str(summary["final_run_statuses"])
            + "; observed_bend_statuses=" + str(summary["observed_bend_statuses"])
            + "; expected a completed Manual 3D run with B02 COLLISION; "
              "the current file may be a different Mac-local debug journal. "
              "Select the original dated Windows debug JSON copied onto this Mac."
        )
    for key in ("part_path", "unfold_name", "punch_path", "die_path"):
        if not isinstance(last.get(key), str) or not last[key]:
            raise BlockerReplayError("missing_" + key)
    if not isinstance(last["reverse_axis"], bool):
        raise BlockerReplayError("missing_exact_reverse_axis")
    if not isinstance(last["virtual_tool_length"], bool):
        raise BlockerReplayError("missing_exact_virtual_tool_mode")
    sections = last["tool_sections"]
    if not isinstance(sections, dict) or any(
        sections.get(kind) for kind in ("punch", "die")
    ):
        # Sectioned physical assemblies require the selected offsets and
        # exact machine assembly, never silently replay one whole prism.
        raise BlockerReplayError("sectioned_tool_assembly_not_supported_in_replay")
    if (not isinstance(last["requested_order"], list)
            or bend_id not in last["requested_order"]):
        raise BlockerReplayError("missing_manual_order")
    if not isinstance(last["geometry"].get("bends"), list):
        raise BlockerReplayError("missing_source_bend_geometry")
    return last


def inspect_source_bend_geometry(source_bends, logged_bends, *, tolerance_mm=1e-3):
    """Compare source vs journal axes BEFORE refusing an unmatched model.

    Read-only, independent of FreeCAD/OCC. A common translation or reversed
    axis direction is diagnostic evidence, NEVER approval to bypass the
    strict exact-source/placement gate or classify a collision.
    """
    expected = {b["id"]: b for b in logged_bends
                if isinstance(b, dict) and isinstance(b.get("id"), str)}
    source_ids = set(source_bends)
    logged_ids = set(expected)
    result = {
        "status": "UNVERIFIED_GEOMETRY",
        "source_bend_count": len(source_bends),
        "recorded_bend_count": len(logged_bends),
        "source_only_bend_ids": sorted(source_ids - logged_ids),
        "journal_only_bend_ids": sorted(logged_ids - source_ids),
        "axis_rows": [],
        "exact_axis_count": 0,
        "reversed_axis_count": 0,
        "mismatched_axis_count": 0,
        "max_endpoint_error_mm": None,
        "all_lengths_equal_within_tolerance": False,
        "uniform_translation_candidate_xyz_mm": None,
        "translation_only_candidate": False,
        "note": (
            "DIAGNOSTIC_ONLY; coordinate-compatible under translation or "
            "axis reversal is NOT proof of identical FCStd source or "
            "valid collision replay"
        ),
    }
    translations = []
    length_equal = True
    all_angle_equal = True
    maximum_error = 0.0
    for bid in sorted(source_ids & logged_ids):
        row = {"bend_id": bid}
        try:
            raw_axis = source_bends[bid].axis_xyz
            log_axis = expected[bid]["axis_xyz"]
            if (len(raw_axis) != 2 or len(log_axis) != 2
                    or any(len(x) != 3 for x in (*raw_axis, *log_axis))):
                raise ValueError("invalid_axis_coordinates")
            actual = tuple(tuple(float(x) for x in point) for point in raw_axis)
            journal = tuple(tuple(float(x) for x in point) for point in log_axis)
            if not all(math.isfinite(v) for point in (*actual, *journal) for v in point):
                raise ValueError("nonfinite_axis_coordinates")
            actual_angle = float(source_bends[bid].angle_deg)
            recorded_angle = float(expected[bid]["angle_deg"])
            if not all(map(math.isfinite, (actual_angle, recorded_angle))):
                raise ValueError("nonfinite_angle")
            angle_error = abs(actual_angle - recorded_angle)
            all_angle_equal &= angle_error <= 1e-6
            actual_len = math.dist(*actual)
            journal_len = math.dist(*journal)
            length_diff = abs(actual_len - journal_len)
            length_equal &= length_diff <= tolerance_mm
            direct = max(math.dist(actual[i], journal[i]) for i in (0, 1))
            swapped = max(math.dist(actual[i], journal[1-i]) for i in (0, 1))
            reversed_axis = swapped < direct
            matched_journal = (journal[1], journal[0]) if reversed_axis else journal
            # Keep the original source point order in the report. No
            # reorientation is applied to the physical FoldState.
            deltas = tuple(
                tuple(actual[i][j] - matched_journal[i][j] for j in range(3))
                for i in (0, 1)
            )
            endpoint_error = min(direct, swapped)
            maximum_error = max(maximum_error, endpoint_error)
            if endpoint_error <= tolerance_mm and angle_error <= 1e-6:
                label = "REVERSED_MATCH" if reversed_axis else "EXACT_MATCH"
                result["reversed_axis_count" if reversed_axis
                       else "exact_axis_count"] += 1
            else:
                label = "MISMATCH"
                result["mismatched_axis_count"] += 1
            row.update({
                "comparison": label,
                "actual_axis_xyz_mm": [list(p) for p in actual],
                "journal_axis_xyz_mm": [list(p) for p in journal],
                "actual_angle_deg": actual_angle,
                "journal_angle_deg": recorded_angle,
                "angle_error_deg": angle_error,
                "actual_length_mm": actual_len,
                "journal_length_mm": journal_len,
                "length_difference_mm": length_diff,
                "endpoint_error_forward_mm": direct,
                "endpoint_error_reversed_mm": swapped,
                "best_endpoint_error_mm": endpoint_error,
                "preferred_direction_reversed": reversed_axis,
                "endpoint_deltas_xyz_mm": [list(p) for p in deltas],
            })
            if length_diff <= tolerance_mm and angle_error <= 1e-6:
                translations.extend(deltas)
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError) as exc:
            row.update({"comparison": "UNREADABLE",
                        "reason": type(exc).__name__ + ":" + str(exc)[:100]})
            length_equal = False
            all_angle_equal = False
        result["axis_rows"].append(row)
    full_ids = source_ids == logged_ids and len(expected) == len(logged_bends)
    result["max_endpoint_error_mm"] = maximum_error if full_ids else None
    result["all_lengths_equal_within_tolerance"] = bool(
        full_ids and length_equal and all_angle_equal
    )
    if full_ids and translations and length_equal and all_angle_equal:
        sample = translations[0]
        if all(math.dist(t, sample) <= tolerance_mm for t in translations):
            result["uniform_translation_candidate_xyz_mm"] = list(sample)
            result["translation_only_candidate"] = True
    if (full_ids and result["mismatched_axis_count"] == 0
            and result["reversed_axis_count"] == 0
            and all_angle_equal and length_equal):
        result["status"] = "EXACT_AXIS_MATCH"
    elif result["translation_only_candidate"]:
        result["status"] = "UNIFORM_TRANSLATION_CANDIDATE"
    else:
        result["status"] = "AXES_DIFFER"
    return result


def verify_source_bends(source_bends, logged_bends, *, tolerance_mm=1e-3):
    """Reject geometry changes with mismatched stable IDs, angles or axes."""
    expected = {b["id"]: b for b in logged_bends}
    if len(expected) != len(logged_bends) or set(source_bends) != set(expected):
        raise BlockerReplayError("source_bend_ids_changed")
    for bid, source in source_bends.items():
        logged = expected[bid]
        src_ang = float(source.angle_deg)
        log_ang = float(logged["angle_deg"])
        if (not math.isfinite(src_ang) or not math.isfinite(log_ang)
                or abs(src_ang - log_ang) > 1e-6):
            raise BlockerReplayError("source_bend_angle_changed:" + bid)
        axis = source.axis_xyz
        recorded = logged["axis_xyz"]
        if (len(axis) != 2 or len(recorded) != 2
                or any(len(p) != 3 for p in axis)
                or any(len(q) != 3 for q in recorded)):
            raise BlockerReplayError("source_bend_axis_missing:" + bid)
        for p, q in zip(axis, recorded):
            for a, b in zip(p, q):
                actual, expected_coord = float(a), float(b)
                if (not math.isfinite(actual) or not math.isfinite(expected_coord)
                        or abs(actual - expected_coord) > tolerance_mm):
                    raise BlockerReplayError("source_bend_axis_changed:" + bid)


def verify_sample_pose(actual, recorded, *, tolerance_mm=1e-3, rotation_tolerance=1e-7):
    """Require independently computed native placement equals logged pose."""
    try:
        expected_r = recorded["rotation"]
        expected_t = recorded["translation"]
        if len(expected_r) != 3 or any(len(row) != 3 for row in expected_r) or len(expected_t) != 3:
            raise ValueError("placement_dimension")
        r_err = max(abs(float(actual.rotation[i][j]) - float(expected_r[i][j]))
                    for i in range(3) for j in range(3))
        t_err = max(abs(float(actual.translation[i]) - float(expected_t[i]))
                    for i in range(3))
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise BlockerReplayError("invalid_logged_pose") from exc
    if not math.isfinite(r_err) or not math.isfinite(t_err) or r_err > rotation_tolerance or t_err > tolerance_mm:
        raise BlockerReplayError("native_placement_mismatch")
    return {"rotation_max_abs_error": r_err, "translation_max_abs_error_mm": t_err}


def _original_basename(raw: str) -> str:
    """Recover a recorded Windows basename even when running under macOS."""
    path = str(raw or "")
    if len(path) >= 3 and path[1] == ":" and path[2] in "/\\":
        return PureWindowsPath(path).name
    if "\\" in path:
        return PureWindowsPath(path).name
    return Path(path).name


def _require_local_source(candidate, *, label: str) -> Path:
    """Never treat a foreign absolute path as valid native FreeCAD source."""
    resolved = Path(candidate).expanduser()
    if not resolved.is_file():
        raise BlockerReplayError(
            f"missing_local_{label}: {resolved}. "
            f"Provide local {label}=... (or part=...) when replaying a journal "
            "on another computer."
        )
    return resolved.resolve(strict=True)


def resolve_replay_sources(
    evidence: dict, *,
    part=None, punch=None, die=None, source_root=None,
) -> dict:
    """Map logged paths to local files without rewriting evidence.

    An explicitly supplied tool MUST keep the recorded basename/Tool ID.
    The original journal has no input-binary hashes: even matching names
    and geometry cannot independently prove the source bytes are identical.
    """
    root = (Path(source_root).expanduser().resolve()
            if source_root is not None
            else Path(__file__).resolve().parents[1])
    recorded_part = str(evidence["part_path"])
    original_part_name = _original_basename(recorded_part)

    if part is not None:
        local_part = _require_local_source(part, label="part")
        part_choice = "explicit_override"
    else:
        local_part = None
        for candidate in (
            Path(recorded_part).expanduser(),
            desktop_directory() / original_part_name,
            desktop_directory() / "demo" / original_part_name,
            root / "demo" / "parts" / original_part_name,
        ):
            if candidate.is_file():
                local_part = candidate.resolve(strict=True)
                break
        if local_part is None:
            raise BlockerReplayError(
                "missing_local_part: journal references "
                f"{recorded_part!r}. Supply part=<local FCStd path> "
                "or copy the exact original file to the desktop demo folder."
            )
        part_choice = "matched_logged_basename"

    sources = {"part": local_part}
    mapping = {"part": part_choice}
    for kind, override in (("punch", punch), ("die", die)):
        recorded = str(evidence[kind + "_path"])
        expected_name = _original_basename(recorded)
        if override is not None:
            path = _require_local_source(override, label=kind)
            if path.name != expected_name:
                raise BlockerReplayError(
                    f"{kind}_tool_id_mismatch: expected {expected_name!r}, "
                    f"supplied {path.name!r}"
                )
            mapping[kind] = "explicit_override"
        else:
            stored = Path(recorded).expanduser()
            library = root / "toollibrary" / kind / expected_name
            if stored.is_file():
                path = stored.resolve(strict=True)
                mapping[kind] = "logged_native_path"
            elif library.is_file():
                path = library.resolve(strict=True)
                mapping[kind] = "same_named_local_toollibrary_source"
            else:
                raise BlockerReplayError(
                    f"missing_local_{kind}: journal references "
                    f"{recorded!r}; expected current addon tool library "
                    f"{library}. Supply {kind}=<local path to {expected_name}>"
                )
        sources[kind] = path
    sources["mapping"] = mapping
    sources["logged_windows_paths"] = {
        "part": recorded_part,
        "punch": evidence["punch_path"],
        "die": evidence["die_path"],
    }
    return sources


def run(journal_path, *, bend_id="B02", output=None, part=None,
        punch=None, die=None, unfold_name=None) -> dict:
    """Test recorded first-blocking pose only; no full joined sheet or search."""
    import FreeCAD as App
    from freecad_smbs.commands.pressbrake_2d import build_part_session
    from smbs_core.kinematics2d.panel_kinematics import FoldState
    from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D
    from smbs_core.kinematics2d.native_bend_solid_3d import build_multi_bend_sheet_components
    from smbs_core.kinematics2d.native_tool_loader import (
        load_native_punch, load_native_die, set_punch_ram_z,
    )
    from smbs_core.kinematics2d.component_collision_oracle import screen_machine_component_overlap
    from tools.run_native_benchmark import sha256_file
    from smbs_core.kinematics2d.runtime_build_identity import (
        loaded_native_module_sources, runtime_source_fingerprint,
    )

    started = perf_counter()
    journal_path = Path(journal_path).expanduser().resolve(strict=True)
    src = json.loads(journal_path.read_text(encoding="utf-8"))
    evidence = select_blocker_evidence(src, bend_id=bend_id)
    # A Windows journal may be replayed in macOS FreeCAD. Never resolve its
    # C:/... absolute paths through POSIX pathlib (they become a fake C:
    # relative directory); map source paths explicitly and verify geometry.
    local_sources = resolve_replay_sources(
        evidence, part=part, punch=punch, die=die,
    )
    part_path = local_sources["part"]
    punch_path = local_sources["punch"]
    die_path = local_sources["die"]
    name = unfold_name or evidence["unfold_name"]
    output_path = (Path(output).expanduser().resolve() if output else
                   journal_path.parent / "SMBS_component_blocker_replay.json")
    if output_path in (journal_path, part_path, punch_path, die_path):
        raise BlockerReplayError("report_must_not_overwrite_input")

    record = {
        "schema_version": "smbs.component_blocker_replay.v1",
        "status": "UNVERIFIED",
        "safety_scope": "ONE_RECORDED_COLLISION_POSE_ONLY; NEVER_CLEAR_OR_CONTINUOUSLY_CERTIFIED",
        "bend_id": bend_id,
        "source": {
            "part_sha256": sha256_file(part_path),
            "punch_sha256": sha256_file(punch_path),
            "die_sha256": sha256_file(die_path),
            "source_hashes_present_in_original_journal": False,
            "matching_original_source_bytes": "NOT_PROVEN",
        },
        "source_path_mapping": {
            "method": local_sources["mapping"],
            "logged_paths": local_sources["logged_windows_paths"],
            "resolved_paths": {
                key: str(local_sources[key]) for key in ("part", "punch", "die")
            },
            "warning": (
                "RECORDED_SOURCE_BINARY_HASHES_MISSING;"
                "SAME_TOOL_ID_AND_GEOMETRY_NOT_BYTE_IDENTICAL_PROOF"
            ),
        },
        "sampled_blocking_u": evidence["first_sampled_blocking_u"],
        "components": [],
        "runtime": {
            "freecad_version": ".".join(map(str, App.Version())),
            "occ_version": str(getattr(App, "OCC_VERSION", "NOT_RECORDED")),
            "installed_source": runtime_source_fingerprint(
                Path(__file__).resolve().parents[1]
            ),
            "loaded_native_module_sources": loaded_native_module_sources(),
        },
    }
    existing = set(App.listDocuments())
    previous_name = getattr(getattr(App, "ActiveDocument", None), "Name", None)
    opened = None
    try:
        opened = App.openDocument(str(part_path), True)
        selected = opened.getObject(name)
        if selected is None:
            raise BlockerReplayError("unfold_not_found:" + name)
        session = build_part_session(opened, [selected])
        record["source_geometry_comparison"] = inspect_source_bend_geometry(
            session.fold_state.bends, evidence["geometry"]["bends"],
        )
        verify_source_bends(
            session.fold_state.bends, evidence["geometry"]["bends"],
        )
        if session.fold_state.fixed_panel != evidence["geometry"].get("fixed_panel"):
            raise BlockerReplayError("fixed_panel_changed")

        source = session.fold_state
        state = FoldState(source.graph, source.fixed_panel)
        pose = evidence["pose"]
        for bid, angle in (pose.get("completed_angles") or {}).items():
            if bid not in state.bends or bid == bend_id:
                raise BlockerReplayError("unknown_or_duplicate_completed_bend")
            state.set_angle(bid, float(angle))
        state.set_angle(bend_id, float(pose["signed_angle_deg"]))
        actual = WorkpiecePlacement3D.compute_placement(
            state, bend_id,
            support_plane_zm=float(pose["support_plane_zm"]),
            desired_midpoint_xm=0.0, align_bisector=True,
            align_bend_arc_center=True,
            reverse_axis=evidence["reverse_axis"],
        )
        record["placement_equality"] = verify_sample_pose(actual, pose)
        # No multiFuse: build authoritative isolated native solids only.
        components = build_multi_bend_sheet_components(
            state, session.panel_build,
            segments=int(evidence["config"].get("arc_segments", 128)),
        )
        by_id = dict(components.named_shapes())
        expected_count = len(session.panel_build.panels) + len(state.ordered_bends)
        if len(by_id) != expected_count or len(components.named_shapes()) != expected_count:
            raise BlockerReplayError("incomplete_component_set")

        length = math.dist(*state.bends[bend_id].axis_xyz)
        virtual_length = length if evidence["virtual_tool_length"] else None
        punch_shape = set_punch_ram_z(load_native_punch(
            punch_path, virtual_length_mm=virtual_length
        ), float(pose["ram_z_mm"])).machine_shape
        die_shape = load_native_die(
            die_path, virtual_length_mm=virtual_length
        ).machine_shape
        matrix = App.Matrix(
            actual.rotation[0][0], actual.rotation[0][1], actual.rotation[0][2], actual.translation[0],
            actual.rotation[1][0], actual.rotation[1][1], actual.rotation[1][2], actual.translation[1],
            actual.rotation[2][0], actual.rotation[2][1], actual.rotation[2][2], actual.translation[2],
            0., 0., 0., 1.,
        )
        tools = {"PUNCH": punch_shape, "DIE": die_shape}
        tolerance = float(evidence["config"].get("volume_tolerance_mm3", 1e-4))
        all_positive = True
        for component_id, expected in evidence["attributed_hits"].items():
            if component_id not in by_id:
                raise BlockerReplayError("attributed_component_not_in_source:" + component_id)
            kind = expected["tool"]
            test = screen_machine_component_overlap(
                ((component_id, by_id[component_id]),),
                actual, matrix, kind, tools[kind],
                volume_tolerance_mm3=tolerance, max_exact_candidates=1,
            )
            vol = test.hit.volume_mm3 if test.hit is not None else None
            matched = (vol is not None and abs(vol - expected["volume_mm3"])
                       <= max(1e-3, expected["volume_mm3"] * 0.005))
            all_positive &= bool(matched)
            record["components"].append({
                "component_id": component_id,
                "tool": kind,
                "verdict": test.verdict.value,
                "reason": test.reason,
                "volume_mm3": vol,
                "original_attribution_volume_mm3": expected["volume_mm3"],
                "original_volume_matched": bool(matched),
                "exact_common_calls": test.common_calls,
            })
        record["status"] = (
            "RECORDED_COMPONENT_PENETRATIONS_REPRODUCED"
            if all_positive else "UNVERIFIED_COMPONENT_MISMATCH"
        )
        record["reference_full_oracle_collision_replayed"] = False
        record["whole_sheet_multiFuse_performed"] = False
    except Exception as exc:
        record["status"] = "UNVERIFIED"
        record["error"] = "{}:{}".format(type(exc).__name__, str(exc)[:350])
    finally:
        record["elapsed_s"] = round(perf_counter() - started, 6)
        if opened is not None and opened.Name not in existing:
            App.closeDocument(opened.Name)
        if previous_name and App.getDocument(previous_name) is not None:
            App.setActiveDocument(previous_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("SMBS blocker component replay:", record["status"],
          "elapsed_s", record["elapsed_s"], "saved", output_path)
    return record


if __name__ == "__main__":
    raise SystemExit(
        "Run this script from the FreeCAD Python console: "
        "from tools.replay_native_component_blocker import run"
    )
