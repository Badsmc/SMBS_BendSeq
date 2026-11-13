"""Replay ONE logged real native D6 blocker as exact component OCC checks.

Runs inside FreeCAD Python. This is a read-only forensic experiment and
NEVER claims that missing component hits establish safe clearance.
It deliberately DOES NOT call full-sheet multiFuse or D6 trajectory sampling.

FreeCAD Python console:
    from tools.replay_native_component_blocker import run
    result = run(r"C:/Users/User/Desktop/SMBS_debug_log.json", bend_id="B02")
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from time import perf_counter


class BlockerReplayError(ValueError):
    """Journal or source cannot be matched to one authoritative native pose."""


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
        raise BlockerReplayError("no_completed_native_collision_for_requested_bend")
    for key in ("part_path", "unfold_name", "punch_path", "die_path"):
        if not isinstance(last.get(key), str) or not last[key]:
            raise BlockerReplayError("missing_" + key)
    if not isinstance(last["reverse_axis"], bool):
        raise BlockerReplayError("missing_exact_reverse_axis")
    if not isinstance(last["virtual_tool_length"], bool):
        raise BlockerReplayError("missing_exact_virtual_tool_mode")
    if (not isinstance(last["requested_order"], list)
            or bend_id not in last["requested_order"]):
        raise BlockerReplayError("missing_manual_order")
    if not isinstance(last["geometry"].get("bends"), list):
        raise BlockerReplayError("missing_source_bend_geometry")
    return last


def verify_source_bends(source_bends, logged_bends, *, tolerance_mm=1e-3):
    """Reject geometry changes with mismatched stable IDs, angles or axes."""
    expected = {b["id"]: b for b in logged_bends}
    if len(expected) != len(logged_bends) or set(source_bends) != set(expected):
        raise BlockerReplayError("source_bend_ids_changed")
    for bid, source in source_bends.items():
        logged = expected[bid]
        if abs(float(source.angle_deg) - float(logged["angle_deg"])) > 1e-6:
            raise BlockerReplayError("source_bend_angle_changed:" + bid)
        axis = source.axis_xyz
        recorded = logged["axis_xyz"]
        if len(axis) != 2 or len(recorded) != 2:
            raise BlockerReplayError("source_bend_axis_missing:" + bid)
        if any(abs(float(a) - float(b)) > tolerance_mm
               for p, q in zip(axis, recorded) for a, b in zip(p, q)):
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


def run(journal_path, *, bend_id="B02", output=None, part=None,
        unfold_name=None) -> dict:
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

    started = perf_counter()
    journal_path = Path(journal_path).expanduser().resolve(strict=True)
    src = json.loads(journal_path.read_text(encoding="utf-8"))
    evidence = select_blocker_evidence(src, bend_id=bend_id)
    part_path = Path(part or evidence["part_path"]).expanduser().resolve(strict=True)
    punch_path = Path(evidence["punch_path"]).expanduser().resolve(strict=True)
    die_path = Path(evidence["die_path"]).expanduser().resolve(strict=True)
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
        "sampled_blocking_u": evidence["first_sampled_blocking_u"],
        "components": [],
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
