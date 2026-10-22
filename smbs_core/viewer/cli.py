"""
cli.py - Command-line interface for SMBS Bend Sequence Viewer (Stage 4, S.14/S.15a).

Usage:
  python -m smbs_core.viewer.cli view <run_dir> [--direction backward] [--dump-frames DIR] [--render-png DIR] [--json] [--allow-mock-backend]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from smbs_core.viewer.graph_builder import GraphBuilderError, build_graphs
from smbs_core.viewer.player import Frame, Player, PlayerError
from smbs_core.viewer.replayer import ReplayerError
from smbs_core.viewer.run_loader import RunLoaderError, load_run


def parse_stride(value: str) -> int:
    try:
        val = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Stride must be an integer, got: '{value}'") from e
    if val < 1:
        raise argparse.ArgumentTypeError(f"Stride must be >= 1, got: {val}")
    return val


def parse_view_dir(value: str) -> Tuple[float, float, float]:
    parts = value.split(",")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(f"View direction must have 3 comma-separated floats, got: '{value}'")
    try:
        vx, vy, vz = float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Invalid float components in view-dir: '{value}'") from e
    if abs(vx) < 1e-6 and abs(vy) < 1e-6 and abs(vz) < 1e-6:
        raise argparse.ArgumentTypeError("View direction vector cannot be zero length (0,0,0)")
    return vx, vy, vz


def frame_to_dict(frame: Frame, backend_kind: str) -> Dict[str, Any]:
    sv = frame.step_view
    state = sv.state

    state_summary = {
        "folded": sorted(list(state.folded_bends)),
        "remaining": sorted(list(state.remaining_bends)),
        "orientation": list(state.orientation),
    }

    return {
        "frame_index": frame.frame_index,
        "step_index": sv.step_index,
        "bend_id": sv.bend_id,
        "ui_direction": sv.ui_direction,
        "planner_status": sv.planner_status,
        "collision_status": sv.collision_status,
        "tooling_status": sv.tooling_status,
        "alignment_status": sv.alignment_status,
        "geometry_precision": sv.geometry_precision,
        "geometry_warning": sv.geometry_warning,
        "backend_kind": backend_kind,
        "state_summary": state_summary,
        "verification_status": frame.verification_status,
        "production_eligible": frame.production_eligible,
        "diagnostics": list(frame.diagnostics),
        "step_verification": frame.step_verification,
    }


def render_table(
    frames: List[Frame],
    verification_status: str,
    production_eligible: Optional[bool],
    diagnostics: List[Any],
    backend_kind: str
) -> str:
    lines = []
    header = f"{'step':<5} | {'bend_id':<18} | {'dir':<5} | {'planner':<10} | {'collision':<10} | {'tooling':<10} | {'alignment':<10}"
    sep = "-" * len(header)
    lines.append(header)
    lines.append(sep)

    for f in frames:
        sv = f.step_view
        st_idx = str(sv.step_index)
        b_id = sv.bend_id if sv.bend_id else "-"
        u_dir = sv.ui_direction if sv.ui_direction else "-"
        p_st = sv.planner_status if sv.planner_status else "-"
        c_st = sv.collision_status if sv.collision_status else "-"
        t_st = sv.tooling_status if sv.tooling_status else "-"
        a_st = sv.alignment_status if sv.alignment_status else "-"

        line = f"{st_idx:<5} | {b_id:<18} | {u_dir:<5} | {p_st:<10} | {c_st:<10} | {t_st:<10} | {a_st:<10}"
        lines.append(line)

    lines.append(sep)
    lines.append(f"verification_status: {verification_status}")
    lines.append(f"production_eligible: {'null' if production_eligible is None else str(production_eligible).lower()}")
    backend_str = "OCC" if backend_kind == "OCC" else "MOCK (preview)"
    lines.append(f"backend: {backend_str}")

    if diagnostics:
        lines.append("diagnostics:")
        for d in diagnostics:
            if isinstance(d, dict):
                code = d.get("code", "DIAG")
                msg = d.get("message", "")
                lines.append(f"  - [{code}] {msg}")
            else:
                lines.append(f"  - {d}")

    return "\n".join(lines)


def run_view(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)

    # Validate render_png constraints
    if args.render_png and args.allow_mock_backend:
        sys.stderr.write("render-png requires OCC backend; --allow-mock-backend is structural-only\n")
        return 2

    # Backend selection: explicit, never silent (invariant #4)
    initial_shape = None
    if args.allow_mock_backend:
        from smbs_core.geometry_backend.backends.mock_backend import MockBackend
        backend = MockBackend()
        backend_kind = "MOCK"
        initial_shape = {"type": "mock_shape", "id": "cli_mock_shape"}
        sys.stderr.write(
            "WARNING: --allow-mock-backend requested. Geometry will be placeholder data. "
            "Output is structural metadata only, not real BREP replay.\n"
        )
    else:
        try:
            from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
            backend = PythonOCCBackend()
            backend_kind = "OCC"
        except ImportError as e:
            sys.stderr.write(
                f"Fatal: OCC backend unavailable ({e}). "
                "Use --allow-mock-backend for structural-only preview (not a real replay).\n"
            )
            return 2

    # Load artifacts and build graphs
    try:
        artifacts = load_run(run_dir)
        graphs = build_graphs(artifacts)
    except (RunLoaderError, GraphBuilderError) as e:
        sys.stderr.write(f"Loader error: {e}\n")
        return 2
    except Exception as e:
        sys.stderr.write(f"Unexpected error loading run: {e}\n")
        return 2

    # Instantiate player
    try:
        player = Player(artifacts=artifacts, graphs=graphs, backend=backend, initial_shape=initial_shape)
        player.switch_direction(args.direction)
        frames = list(player.frames)
    except (PlayerError, ReplayerError) as e:
        sys.stderr.write(f"Player error: {e}\n")
        return 1
    except Exception as e:
        sys.stderr.write(f"Unexpected player failure: {e}\n")
        return 1

    first_frame = frames[0] if frames else None
    verif_status = first_frame.verification_status if first_frame else "UNVERIFIED"
    prod_elig = first_frame.production_eligible if first_frame else None
    diags = list(first_frame.diagnostics) if first_frame else []

    # Dump frames if requested
    if args.dump_frames:
        dump_dir = Path(args.dump_frames)
        dump_dir.mkdir(parents=True, exist_ok=True)
        for f in frames:
            f_dict = frame_to_dict(f, backend_kind=backend_kind)
            out_file = dump_dir / f"frame_{f.frame_index:03d}.json"
            out_file.write_text(json.dumps(f_dict, indent=2))

    # Render PNG sequence if requested
    if args.render_png:
        from smbs_core.viewer.render import render_frame_png, RenderError, RENDER_KIND_HLR
        render_dir = Path(args.render_png)
        render_dir.mkdir(parents=True, exist_ok=True)

        rendered_frames = []
        render_kinds = set()
        stride = args.render_stride
        total_count = len(frames)

        for i, frame in enumerate(frames):
            # Render every N-th frame, and ALWAYS render the final frame
            if i % stride != 0 and i != total_count - 1:
                continue

            sv = frame.step_view
            out_path = render_dir / f"frame_{i:03d}.png"
            if sv.step_index == 0:
                title = "step 0 (initial fully folded)"
            else:
                title = f"step {sv.step_index}: bend {sv.bend_id} [{sv.ui_direction}]"

            subtitle = f"planner={sv.planner_status or '-'} | coll={sv.collision_status or '-'} | verif={frame.verification_status}"

            try:
                rk = render_frame_png(
                    shape=sv.state.shape,
                    output_path=out_path,
                    width=args.render_width,
                    height=args.render_height,
                    title=title,
                    subtitle=subtitle,
                    view_dir=args.view_dir
                )
            except RenderError as e:
                sys.stderr.write(f"Render error on frame {i}: {e}\n")
                return 1

            render_kinds.add(rk)
            rendered_frames.append({
                "index": i,
                "file": out_path.name,
                "bend_id": sv.bend_id,
                "render_kind": rk
            })

        manifest = {
            "run_dir": str(run_dir.resolve()),
            "backend_kind": backend_kind,
            "render_kind": RENDER_KIND_HLR if render_kinds == {RENDER_KIND_HLR} else "MIXED",
            "render_kinds_seen": sorted(list(render_kinds)),
            "verification_status": verif_status,
            "production_eligible": prod_elig,
            "diagnostics": diags,
            "kl12_warning": frames[0].step_view.geometry_warning if frames else None,
            "geometry_precision": (
                frames[0].step_view.geometry_precision if frames else "PREVIEW_ONLY"
            ),
            "total_frames": len(frames),
            "rendered_frames": len(rendered_frames),
            "stride": stride,
            "view_dir": list(args.view_dir),
            "frames": rendered_frames,
        }
        (render_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        sys.stdout.write(f"Rendered {len(rendered_frames)} frames to {render_dir} (manifest.json written)\n")

    # Output representation
    if args.json:
        payload = {
            "run_dir": str(run_dir.resolve()),
            "total_frames": len(frames),
            "backend_kind": backend_kind,
            "frames": [frame_to_dict(f, backend_kind=backend_kind) for f in frames],
            "verification_status": verif_status,
            "production_eligible": prod_elig,
            "diagnostics": diags,
        }
        sys.stdout.write(json.dumps(payload) + "\n")
    else:
        table = render_table(frames, verif_status, prod_elig, diags, backend_kind=backend_kind)
        sys.stdout.write(table + "\n")

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SMBS Headless Viewer CLI (Stage 4)")
    subparsers = parser.add_subparsers(dest="command")

    view_parser = subparsers.add_parser("view", help="View timeline frames of a pipeline run")
    view_parser.add_argument("run_dir", type=str, help="Directory of pipeline run")
    view_parser.add_argument(
        "--direction",
        type=str,
        default="backward",
        choices=["backward", "forward"],
        help="Playback direction: 'backward' (unfold) or 'forward' (fold)"
    )
    view_parser.add_argument("--dump-frames", type=str, default=None, help="Directory to dump frame JSON files")
    view_parser.add_argument("--json", action="store_true", help="Output single JSON representation to stdout")
    view_parser.add_argument(
        "--allow-mock-backend",
        action="store_true",
        help="Explicitly allow MockBackend for preview without OCC (structural metadata only)"
    )
    view_parser.add_argument(
        "--render-png",
        type=str,
        default=None,
        help="Render each frame to PNG into this directory (requires OCC backend)"
    )
    view_parser.add_argument(
        "--render-stride",
        type=parse_stride,
        default=1,
        help="Render every N-th frame (default: 1 = all)"
    )
    view_parser.add_argument("--render-width", type=int, default=800, help="Width of rendered PNG")
    view_parser.add_argument("--render-height", type=int, default=600, help="Height of rendered PNG")
    view_parser.add_argument(
        "--view-dir",
        type=parse_view_dir,
        default=(1.0, 1.0, 1.0),
        help="Camera direction as 'x,y,z' (default: '1.0,1.0,1.0' iso)"
    )

    if argv is None:
        argv = sys.argv[1:]

    # Parse arguments
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2

    if args.command == "view":
        return run_view(args)
    else:
        parser.print_help()
        return 2


if __name__ == "__main__":
    sys.exit(main())
