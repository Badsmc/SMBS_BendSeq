"""
Analytical calculation of expected metrics for L-bracket synthetic fixture.
Parameters:
  l1 = 95.5 mm (flange 1 flat length)
  l2 = 47.5 mm (flange 2 flat length)
  width = 50.0 mm
  thickness = 1.5 mm
  radius_internal = 1.0 mm
  bend angle = +90 deg
Pure analytical mathematics without OCC.
"""

import json
import math
from pathlib import Path

def compute_expected():
    l1 = 95.5
    l2 = 47.5
    w = 50.0
    t = 1.5
    r = 1.0
    R = r + t  # 2.5 mm

    # 1. Volume
    # V = V_flange1 + V_flange2 + V_bend_sector
    # V_bend_sector = (alpha / 2) * (R^2 - r^2) * w, with alpha = pi / 2
    v_flanges = (l1 + l2) * w * t
    v_sector = (math.pi / 2.0) * (R**2 - r**2) / 2.0 * w
    volume_mm3 = v_flanges + v_sector

    # 2. Bounding Box
    # Base flange along X: [0, l1] -> [0, 95.5]
    # Bend sector along X: [l1, l1 + R] -> [95.5, 98.0]
    # Total dx = l1 + R = 95.5 + 2.5 = 98.0 mm
    # Total dy = w = 50.0 mm
    # Along Z:
    # Base flange in Z: [0, t] -> [0, 1.5]
    # Bend sector center at Z = R = 2.5, inner bottom at Z = 0
    # Flange 2 extends in +Z from Z = R to Z = R + l2 = 2.5 + 47.5 = 50.0 mm
    # Total dz = R + l2 = 2.5 + 47.5 = 50.0 mm
    bbox_mm = [l1 + R, w, R + l2]

    # 3. Surface Area
    # Flange planar major faces: 2 for flange 1 (l1 * w each), 2 for flange 2 (l2 * w each)
    a_flanges = 2.0 * (l1 + l2) * w
    # Cylindrical bend faces: 1 inner (radius r) + 1 outer (radius R)
    # Area = (alpha * r * w) + (alpha * R * w) = (pi / 2) * (r + R) * w
    a_cyl = (math.pi / 2.0) * (r + R) * w
    # 2 end faces at the tips of the flanges: area = w * t each
    a_ends = 2.0 * (w * t)
    # 2 lateral side profile faces (at Y=0 and Y=w):
    # Cross-section area of profile = (l1 * t) + (l2 * t) + (alpha / 2) * (R^2 - r^2)
    a_side_profile = (l1 + l2) * t + (math.pi / 4.0) * (R**2 - r**2)
    surface_area_mm2 = a_flanges + a_cyl + a_ends + 2.0 * a_side_profile

    # 4. Topology Counts
    # Planar faces:
    # - 2 for flange 1 (top, bottom)
    # - 2 for flange 2 (inner, outer)
    # - 2 end cut faces (at X=0 and Z=R+l2)
    # - 2 lateral side faces (at Y=0 and Y=w)
    # Total planar = 8
    # Cylindrical faces: 2 (1 inner concave, 1 outer convex)
    # Total faces = 10
    face_count = 10
    cylindrical_face_count = 2

    formulas = {
        "volume": "V = (l1 + l2) * w * t + (pi / 4) * ((r + t)**2 - r**2) * w",
        "bbox": "[dx, dy, dz] = [l1 + r + t, w, r + t + l2]",
        "surface_area": "A = 2 * (l1 + l2) * w + (pi / 2) * (2*r + t) * w + 2 * (w * t) + 2 * ((l1 + l2) * t + (pi / 4) * ((r + t)**2 - r**2))",
        "face_count": "10 faces: 4 flange major faces + 2 end faces + 2 side profile faces + 2 cylindrical faces",
        "cylindrical_face_count": "2 faces: 1 inner cylinder (radius r) + 1 outer cylinder (radius r + t)"
    }

    result = {
        "fixture_id": "l_bracket",
        "volume_mm3": round(volume_mm3, 6),
        "bbox_mm": [round(x, 6) for x in bbox_mm],
        "face_count": face_count,
        "cylindrical_face_count": cylindrical_face_count,
        "surface_area_mm2": round(surface_area_mm2, 6),
        "formulas": formulas
    }

    out_path = Path(__file__).parent / "l_bracket.expected.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"Wrote analytical expected metrics to {out_path}")
    return result

if __name__ == "__main__":
    compute_expected()
