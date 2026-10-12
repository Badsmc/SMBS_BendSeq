"""
Analytical calculation of expected metrics for U-bracket synthetic fixture.
Parameters:
  l1 = 60.0 mm (leg 1 flat length)
  l2 = 40.0 mm (middle base web flat length)
  l3 = 60.0 mm (leg 2 flat length)
  width = 50.0 mm
  thickness = 1.5 mm
  radius_internal = 1.0 mm
  bend angles = +90 deg, +90 deg (both in the same direction, turning up)
Pure analytical mathematics without OCC.
"""

import json
import math
from pathlib import Path

def compute_expected():
    l1 = 60.0
    l2 = 40.0
    l3 = 60.0
    w = 50.0
    t = 1.5
    r = 1.0
    R = r + t  # 2.5 mm

    # 1. Volume
    # V = V_flanges + 2 * V_sector
    # V_sector = (alpha / 2) * (R^2 - r^2) * w, with alpha = pi / 2
    v_flanges = (l1 + l2 + l3) * w * t
    v_sectors = 2.0 * ((math.pi / 2.0) * (R**2 - r**2) / 2.0 * w)
    volume_mm3 = v_flanges + v_sectors

    # 2. Bounding Box
    # Base flange along X: [R, R + l2] -> [2.5, 42.5]
    # Bend 1 on left: [0, R] -> [0, 2.5]
    # Bend 2 on right: [R + l2, 2*R + l2] -> [42.5, 45.0]
    # Total dx = 2 * R + l2 = 2 * 2.5 + 40.0 = 45.0 mm
    # Total dy = w = 50.0 mm
    # Along Z:
    # Base flange at Z: [0, t] -> [0, 1.5]
    # Bends start at Z=0, tangent at Z=R=2.5 mm
    # Legs 1 and 2 extend in +Z from Z = R to Z = R + l1 = 2.5 + 60.0 = 62.5 mm
    # Total dz = R + l1 = 2.5 + 60.0 = 62.5 mm
    bbox_mm = [2.0 * R + l2, w, R + l1]

    # 3. Surface Area
    # Flange planar major faces:
    # 2 for base flange (l2 * w each) + 2 for leg 1 (l1 * w each) + 2 for leg 2 (l3 * w each)
    a_flanges = 2.0 * (l1 + l2 + l3) * w
    # Cylindrical bend faces: 2 inner (radius r) + 2 outer (radius R)
    # Area = 2 * ((pi / 2) * r * w) + 2 * ((pi / 2) * R * w) = pi * (r + R) * w
    a_cyl = 2.0 * (math.pi / 2.0) * (r + R) * w
    # 2 end faces at the top of the two legs: area = w * t each
    a_ends = 2.0 * (w * t)
    # 2 lateral side profile faces (at Y=0 and Y=w):
    # Cross-section area of profile = (l1 + l2 + l3) * t + 2 * ((pi / 4) * (R^2 - r^2))
    a_side_profile = (l1 + l2 + l3) * t + 2.0 * (math.pi / 4.0) * (R**2 - r**2)
    surface_area_mm2 = a_flanges + a_cyl + a_ends + 2.0 * a_side_profile

    # 4. Topology Counts
    # Planar faces:
    # - 2 for base flange (top, bottom)
    # - 2 for leg 1 (inner, outer)
    # - 2 for leg 2 (inner, outer)
    # - 2 end cut faces at the tips of the legs
    # - 2 lateral side faces (at Y=0 and Y=w)
    # Total planar = 10
    # Cylindrical faces: 4 (2 inner concave, 2 outer convex)
    # Total faces = 14
    face_count = 14
    cylindrical_face_count = 4

    formulas = {
        "volume": "V = (l1 + l2 + l3) * w * t + 2 * (pi / 4) * ((r + t)**2 - r**2) * w",
        "bbox": "[dx, dy, dz] = [2*(r + t) + l2, w, (r + t) + l1]",
        "surface_area": "A = 2 * (l1 + l2 + l3) * w + pi * (2*r + t) * w + 2 * (w * t) + 2 * ((l1 + l2 + l3) * t + (pi / 2) * ((r + t)**2 - r**2))",
        "face_count": "14 faces: 6 flange major faces + 2 end faces + 2 side profile faces + 4 cylindrical faces",
        "cylindrical_face_count": "4 faces: 2 inner cylinders (radius r) + 2 outer cylinders (radius r + t)"
    }

    result = {
        "fixture_id": "u_bracket",
        "volume_mm3": round(volume_mm3, 6),
        "bbox_mm": [round(x, 6) for x in bbox_mm],
        "face_count": face_count,
        "cylindrical_face_count": cylindrical_face_count,
        "surface_area_mm2": round(surface_area_mm2, 6),
        "formulas": formulas
    }

    out_path = Path(__file__).parent / "u_bracket.expected.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"Wrote analytical expected metrics to {out_path}")
    return result

if __name__ == "__main__":
    compute_expected()
