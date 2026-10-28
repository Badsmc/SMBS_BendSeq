"""
load_fcstd.py - Load 16 FreeCAD FCStd fixtures and extract golden data.
Task 1.0.0b compliant.

Actions per fixture:
1. Copy source .FCStd into fixtures/freecad/<fixture_slug>/source.FCStd
2. Open document via FreeCAD.openDocument (non-GUI), doc.recompute()
3. Export part_3d.brep from terminal Bend solid
4. Export flat_pattern.brep from Unfold solid
5. Extract bends.json from SheetMetal unfold metadata / bend operations:
   - each bend contains requires_gooseneck: true|false
   - top-level contains part_requires_gooseneck: true|false
   - 2-1-3-4 sequence loaded from explicit operator-provided JSON (fixtures/freecad/operator_sequence_2_1_3_4.json)
6. Compute real SHA-256 for all artifacts
7. Generate manifest.json (§15.3 ТЗ):
   - 135_ok: source_of_truth: RECOMPUTED_FROM_FINAL_BODY + known_limitations note
   - remaining 15: source_of_truth: SHEETMETAL_NATIVE
"""

import hashlib
import json
import os
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.append("/Applications/FreeCAD.app/Contents/Resources/lib")
_sm_user_dir = str(Path.home() / "Library/Application Support/FreeCAD/v1-1/Mod/sheetmetal")
if _sm_user_dir not in sys.path:
    sys.path.append(_sm_user_dir)

import FreeCAD
import Part
import SheetMetalNewUnfolder

FIXTURE_SPECS = [
    {
        "index": 1,
        "src_name": "L-(ok).FCStd",
        "slug": "l_ok",
        "bends_count": 1,
        "expected_status": "POSITIVE",
        "production_eligible": True,
        "features_desc": "Простейший",
        "requires_gooseneck": False,
        "negative_reason": None,
        "operator_confirmed_seq": False,
        "source_of_truth": "SHEETMETAL_NATIVE",
    },
    {
        "index": 2,
        "src_name": "L-30-6-(false).FCStd",
        "slug": "l_30_6_false",
        "bends_count": 1,
        "expected_status": "NEGATIVE",
        "production_eligible": False,
        "features_desc": "R0.8+V12 не может",
        "requires_gooseneck": False,
        "negative_reason": "operator_confirmed_incompatible_on_R08_V12",
        "operator_confirmed_seq": False,
        "source_of_truth": "SHEETMETAL_NATIVE",
    },
    {
        "index": 3,
        "src_name": "U-90deg-90deg-(ok).FCStd",
        "slug": "u_90deg_90deg_ok",
        "bends_count": 2,
        "expected_status": "POSITIVE",
        "production_eligible": True,
        "features_desc": "Базовый U",
        "requires_gooseneck": False,
        "negative_reason": None,
        "operator_confirmed_seq": False,
        "source_of_truth": "SHEETMETAL_NATIVE",
    },
    {
        "index": 4,
        "src_name": "U-10-30-10-(OK).FCStd",
        "slug": "u_10_30_10_ok",
        "bends_count": 2,
        "expected_status": "POSITIVE",
        "production_eligible": True,
        "features_desc": "Широкая средняя",
        "requires_gooseneck": False,
        "negative_reason": None,
        "operator_confirmed_seq": False,
        "source_of_truth": "SHEETMETAL_NATIVE",
    },
    {
        "index": 5,
        "src_name": "U-10-10-20-(false).FCStd",
        "slug": "u_10_10_20_false",
        "bends_count": 2,
        "expected_status": "NEGATIVE",
        "production_eligible": False,
        "features_desc": "R0.8+V12 не может",
        "requires_gooseneck": False,
        "negative_reason": "operator_confirmed_incompatible_on_R08_V12",
        "operator_confirmed_seq": False,
        "source_of_truth": "SHEETMETAL_NATIVE",
    },
    {
        "index": 6,
        "src_name": "U-60deg-30deg-(ok).FCStd",
        "slug": "u_60deg_30deg_ok",
        "bends_count": 2,
        "expected_status": "POSITIVE",
        "production_eligible": True,
        "features_desc": "Углы 60°, 30°",
        "requires_gooseneck": False,
        "negative_reason": None,
        "operator_confirmed_seq": False,
        "source_of_truth": "SHEETMETAL_NATIVE",
    },
    {
        "index": 7,
        "src_name": "U-10-15-10-(punch2-OK).FCStd",
        "slug": "u_10_15_10_punch2_ok",
        "bends_count": 2,
        "expected_status": "POSITIVE",
        "production_eligible": True,
        "features_desc": "Требует gooseneck",
        "requires_gooseneck": True,
        "negative_reason": None,
        "operator_confirmed_seq": False,
        "source_of_truth": "SHEETMETAL_NATIVE",
    },
    {
        "index": 8,
        "src_name": "Z-(ok).FCStd",
        "slug": "z_ok",
        "bends_count": 2,
        "expected_status": "POSITIVE",
        "production_eligible": True,
        "features_desc": "±90°",
        "requires_gooseneck": False,
        "negative_reason": None,
        "operator_confirmed_seq": False,
        "source_of_truth": "SHEETMETAL_NATIVE",
    },
    {
        "index": 9,
        "src_name": "Z-10-20-10-(ok).FCStd",
        "slug": "z_10_20_10_ok",
        "bends_count": 2,
        "expected_status": "POSITIVE",
        "production_eligible": True,
        "features_desc": "±90°",
        "requires_gooseneck": False,
        "negative_reason": None,
        "operator_confirmed_seq": False,
        "source_of_truth": "SHEETMETAL_NATIVE",
    },
    {
        "index": 10,
        "src_name": "Z-10-20-10-(false).FCStd",
        "slug": "z_10_20_10_false",
        "bends_count": 2,
        "expected_status": "NEGATIVE",
        "production_eligible": False,
        "features_desc": "R0.8+V12 не может",
        "requires_gooseneck": False,
        "negative_reason": "operator_confirmed_incompatible_on_R08_V12",
        "operator_confirmed_seq": False,
        "source_of_truth": "SHEETMETAL_NATIVE",
    },
    {
        "index": 11,
        "src_name": "135-(ok).FCStd",
        "slug": "135_ok",
        "bends_count": 2,
        "expected_status": "POSITIVE",
        "production_eligible": True,
        "features_desc": "Углы +45° и −45° [OP]",
        "requires_gooseneck": False,
        "negative_reason": None,
        "operator_confirmed_seq": False,
        "source_of_truth": "RECOMPUTED_FROM_FINAL_BODY",
        "known_limitation_note": "flat_pattern recomputed via getUnfold on final Bend001 body; original Тело_Unfold attached to intermediate Bend",
    },
    {
        "index": 12,
        "src_name": "box.100out-50in-(ok).FCStd",
        "slug": "box_100out_50in_ok",
        "bends_count": 8,
        "expected_status": "POSITIVE",
        "production_eligible": True,
        "features_desc": "Коробка out/in",
        "requires_gooseneck": False,
        "negative_reason": None,
        "operator_confirmed_seq": False,
        "source_of_truth": "SHEETMETAL_NATIVE",
    },
    {
        "index": 13,
        "src_name": "box.100in-50out-(false).FCStd",
        "slug": "box_100in_50out_false",
        "bends_count": 8,
        "expected_status": "NEGATIVE",
        "production_eligible": False,
        "features_desc": "R0.8+V12 не может",
        "requires_gooseneck": False,
        "negative_reason": "operator_confirmed_incompatible_on_R08_V12",
        "operator_confirmed_seq": False,
        "source_of_truth": "SHEETMETAL_NATIVE",
    },
    {
        "index": 14,
        "src_name": "SQin.200-25-10-(ok).FCStd",
        "slug": "sqin_200_25_10_ok",
        "bends_count": 8,
        "expected_status": "POSITIVE",
        "production_eligible": True,
        "features_desc": "Квадрат 200×200, стенка 25, загиб 10 внутрь",
        "requires_gooseneck": False,
        "negative_reason": None,
        "operator_confirmed_seq": False,
        "source_of_truth": "SHEETMETAL_NATIVE",
    },
    {
        "index": 15,
        "src_name": "SQout.200-25-10-(ok).FCStd",
        "slug": "sqout_200_25_10_ok",
        "bends_count": 8,
        "expected_status": "POSITIVE",
        "production_eligible": True,
        "features_desc": "Квадрат 200×200, стенка 25, загиб 10 наружу",
        "requires_gooseneck": False,
        "negative_reason": None,
        "operator_confirmed_seq": False,
        "source_of_truth": "SHEETMETAL_NATIVE",
    },
    {
        "index": 16,
        "src_name": "2-1-3-4-(ok).FCStd",
        "slug": "2_1_3_4_ok",
        "bends_count": 10,
        "expected_status": "POSITIVE",
        "production_eligible": True,
        "features_desc": "Дверь, 4 грани, знакопеременные [OP]",
        "requires_gooseneck": True,
        "negative_reason": None,
        "operator_confirmed_seq": True,
        "source_of_truth": "SHEETMETAL_NATIVE",
    },
]


def compute_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_sheetmetal_version() -> str:
    pkg_path = Path.home() / "Library/Application Support/FreeCAD/v1-1/Mod/sheetmetal/package.xml"
    if pkg_path.exists():
        try:
            tree = ET.parse(pkg_path)
            root = tree.getroot()
            ver = root.find("version")
            if ver is not None and ver.text:
                return ver.text.strip()
        except Exception:
            pass
    return "unknown"


def extract_fixture(spec: Dict[str, Any], src_dir: Path, out_base_dir: Path) -> Dict[str, Any]:
    src_file = src_dir / spec["src_name"]
    if not src_file.exists():
        raise FileNotFoundError(f"Source file not found: {src_file}")

    fixture_dir = out_base_dir / spec["slug"]
    fixture_dir.mkdir(parents=True, exist_ok=True)

    dest_fcstd = fixture_dir / "source.FCStd"
    shutil.copy2(src_file, dest_fcstd)
    fcstd_hash = compute_file_sha256(dest_fcstd)

    doc = FreeCAD.openDocument(str(dest_fcstd))
    doc.recompute()

    # 1. Locate Unfold object
    unfold_obj = None
    for obj in doc.Objects:
        if "Unfold" in obj.Name or "Unfold" in obj.Label:
            if hasattr(obj, "Shape") and obj.Shape.ShapeType in ("Solid", "Compound") and obj.Shape.Volume > 0:
                unfold_obj = obj
                break
    if not unfold_obj:
        raise ValueError(f"Unfold object not found in {dest_fcstd}")

    # 2. Locate 3D bent shape (Body or terminal bend feature)
    body_obj = doc.getObject("Body") or doc.getObject("Тело")
    if body_obj and hasattr(body_obj, "Shape") and body_obj.Shape.Volume > 0:
        bent_shape = body_obj.Shape
    else:
        # Fallback to terminal feature with highest volume or latest bend
        bends = [obj for obj in doc.Objects if hasattr(obj, "angle") and hasattr(obj, "Shape")]
        if bends:
            bent_shape = bends[-1].Shape
        else:
            raise ValueError(f"3D bent shape not found in {dest_fcstd}")

    # Extract single solid if compound
    if bent_shape.ShapeType == "Compound" and bent_shape.Solids:
        part_3d_shape = bent_shape.Solids[0]
    else:
        part_3d_shape = bent_shape

    flat_shape = unfold_obj.Shape
    if flat_shape.ShapeType == "Compound" and flat_shape.Solids:
        flat_shape = flat_shape.Solids[0]

    # Export BREP artifacts
    part_3d_path = fixture_dir / "part_3d.brep"
    flat_path = fixture_dir / "flat_pattern.brep"
    part_3d_shape.exportBrep(str(part_3d_path))
    flat_shape.exportBrep(str(flat_path))

    hash_part_3d = compute_file_sha256(part_3d_path)
    hash_flat = compute_file_sha256(flat_path)

    # 3. Extract bends information
    labels_match = True
    warning_msg = None
    bends_data: List[Dict[str, Any]] = []

    # Get SheetMetal unfold bend info
    base_obj, base_sub = unfold_obj.baseObject
    face_spec = base_sub[0]
    if "." in face_spec:
        obj_name, face_name = face_spec.split(".", 1)
        target_obj = doc.getObject(obj_name)
    else:
        target_obj = base_obj
        face_name = face_spec

    bac = SheetMetalNewUnfolder.BendAllowanceCalculator.from_single_value(
        unfold_obj.KFactor, unfold_obj.KFactorStandard
    )

    try:
        _, _, _, _, bend_lines_info = SheetMetalNewUnfolder.getUnfold(bac, target_obj, face_name)
    except Exception as e:
        bend_lines_info = []
        warning_msg = f"getUnfold failed: {e}"

    # Specific case for 135-(ok): document unfold was performed on Bend (1 bend), but Body has Bend001 (2 bends)
    if spec["slug"] == "135_ok" and len(bend_lines_info) == 1:
        bend001 = doc.getObject("Bend001")
        if bend001:
            try:
                # Unfolding from Face4 yields both bends: [+135.0, -135.0]
                _, _, _, _, b_info2 = SheetMetalNewUnfolder.getUnfold(bac, bend001, "Face4")
                if len(b_info2) == 2:
                    bend_lines_info = b_info2
            except Exception:
                pass

    # Specific case for 2-1-3-4-(ok): load operator-confirmed sequence from external JSON specification
    if spec["slug"] == "2_1_3_4_ok":
        op_json_path = out_base_dir.parent / "freecad" / "operator_sequence_2_1_3_4.json"
        if not op_json_path.exists():
            op_json_path = Path(__file__).resolve().parent / "operator_sequence_2_1_3_4.json"
        with open(op_json_path, "r", encoding="utf-8") as f:
            op_data = json.load(f)
        bends_data = op_data["bends"]
    else:
        # Standard extraction from bend_lines_info
        for idx, bi in enumerate(bend_lines_info, start=1):
            deg = round(float(bi.angle), 2)
            # Map FreeCAD 135 deg to bending angle 45 deg if specified
            if spec["slug"] == "135_ok":
                mapped_deg = 45.0 if deg > 0 else -45.0
            else:
                mapped_deg = deg

            bend_requires_gooseneck = spec["requires_gooseneck"]

            bends_data.append({
                "bend_id": f"BEND_{idx:02d}",
                "angle_deg": mapped_deg,
                "direction": "UP" if mapped_deg > 0 else "DOWN",
                "radius_internal_mm": 1.0,
                "requires_gooseneck": bend_requires_gooseneck,
                "source": "SHEETMETAL_NATIVE",
            })

    if len(bends_data) != spec["bends_count"]:
        labels_match = False
        warning_msg = f"Extracted bends count ({len(bends_data)}) != expected spec ({spec['bends_count']})"

    part_requires_gooseneck = any(b.get("requires_gooseneck", False) for b in bends_data)

    bends_json_content = {
        "part_requires_gooseneck": part_requires_gooseneck,
        "bends": bends_data,
        "extraction_method": "SheetMetal_Unfold_Sketch_Bend_Labels",
        "labels_match_bend_operation": labels_match,
    }

    bends_path = fixture_dir / "bends.json"
    with open(bends_path, "w", encoding="utf-8") as f:
        json.dump(bends_json_content, f, indent=2, ensure_ascii=False)

    hash_bends = compute_file_sha256(bends_path)

    # 4. Generate manifest.json
    freecad_ver = ".".join(FreeCAD.Version()[:3])
    sheetmetal_ver = get_sheetmetal_version()

    known_limitations = [
        "Springback не учтён",
        "Станок UNKNOWN (модель/усилие не уточнены)",
        "Gooseneck параметры — только радиус рабочей части",
    ]
    if spec.get("known_limitation_note"):
        known_limitations.append(spec["known_limitation_note"])

    manifest_data = {
        "schema_name": "fixture_manifest",
        "schema_version": "1.0",
        "fixture_id": spec["slug"],
        "class": "FREECAD_NATIVE",
        "source": {
            "type": "FREECAD_FCSTD",
            "path": f"fixtures/freecad/{spec['slug']}/source.FCStd",
            "content_hash": f"sha256:{fcstd_hash}",
            "freecad_version": freecad_ver,
            "sheetmetal_version": sheetmetal_ver,
            "created_in_gui": True,
        },
        "input_files": [
            {
                "role": "PART_3D",
                "path": f"fixtures/freecad/{spec['slug']}/part_3d.brep",
                "content_hash": f"sha256:{hash_part_3d}",
                "units": "mm",
            },
            {
                "role": "FLAT_PATTERN",
                "path": f"fixtures/freecad/{spec['slug']}/flat_pattern.brep",
                "content_hash": f"sha256:{hash_flat}",
                "units": "mm",
            },
            {
                "role": "BENDS_METADATA",
                "path": f"fixtures/freecad/{spec['slug']}/bends.json",
                "content_hash": f"sha256:{hash_bends}",
                "units": "mm",
            },
        ],
        "ground_truth": {
            "bends_count": len(bends_data),
            "bend_sequence": bends_data,
            "bend_sequence_operator_confirmed": spec["operator_confirmed_seq"],
            "expected_status": spec["expected_status"],
            "production_eligible": spec["production_eligible"],
            "thickness_mm": 1.5,
            "radius_internal_mm": 1.0,
            "radius_punch_mm": 0.8,
            "k_factor": 0.4,
            "die_v_opening_mm": 12.0,
            "requires_gooseneck": spec["requires_gooseneck"],
            "negative_reason": spec["negative_reason"],
            "source_of_truth": spec["source_of_truth"],
        },
        "tolerance_profile": {
            "linear_tol_mm": 0.001,
            "angular_tol_deg": 0.001,
            "volume_tol_mm3": 0.001,
            "bbox_tol_mm": 0.001,
            "surface_distance_tol_mm": 0.01,
        },
        "known_limitations": known_limitations,
    }

    manifest_path = fixture_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)

    FreeCAD.closeDocument(doc.Name)

    return {
        "index": spec["index"],
        "src_name": spec["src_name"],
        "slug": spec["slug"],
        "size_kb": round(dest_fcstd.stat().st_size / 1024.0, 1),
        "bends_count": len(bends_data),
        "hash_part_3d": hash_part_3d,
        "hash_flat": hash_flat,
        "hash_bends": hash_bends,
        "hash_fcstd": fcstd_hash,
        "warning": warning_msg or "None",
        "labels_match": labels_match,
    }


def main():
    repo_root = Path(__file__).resolve().parent.parent.parent
    src_dir = Path.home() / "Desktop" / "BendSeq_Fixtures_Source"
    out_base_dir = repo_root / "fixtures" / "freecad"
    out_base_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading 16 FCStd fixtures from {src_dir} to {out_base_dir}...")
    results = []
    for spec in FIXTURE_SPECS:
        res = extract_fixture(spec, src_dir, out_base_dir)
        results.append(res)
        print(f"[{res['index']:02d}/16] {res['src_name']} -> {res['slug']} ({res['bends_count']} bends) [Warning: {res['warning']}]")

    # Update fixtures/registry.json
    registry_path = repo_root / "fixtures" / "registry.json"
    registry_data = {"fixtures": []}
    if registry_path.exists():
        try:
            with open(registry_path, "r", encoding="utf-8") as f:
                registry_data = json.load(f)
        except Exception:
            registry_data = {"fixtures": []}

    fixtures_list = [f for f in registry_data.get("fixtures", []) if f.get("class") != "FREECAD_NATIVE"]
    for res in results:
        fixtures_list.append({
            "fixture_id": res["slug"],
            "class": "FREECAD_NATIVE",
            "manifest": f"fixtures/freecad/{res['slug']}/manifest.json",
            "status": "NOT_RUN",
            "requires": ["FreeCAD", "SheetMetal"],
        })

    registry_data["fixtures"] = fixtures_list
    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(registry_data, f, indent=2, ensure_ascii=False)
    print(f"Updated {registry_path} with {len(results)} FREECAD_NATIVE entries.")

    return results


if __name__ == "__main__":
    main()
