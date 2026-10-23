"""
Analytical calculation of expected metrics for 4-bend busbar synthetic fixture.
Parameters:
  l1 = 40.0 mm
  l2 = 30.0 mm
  l3 = 50.0 mm
  l4 = 30.0 mm
  l5 = 40.0 mm
  width = 50.0 mm
  thickness = 1.5 mm
  radius_internal = 1.0 mm
  bend angles = +90 deg, -90 deg, -90 deg, +90 deg (stepped busbar with 4 alternating 90 deg bends)
Pure analytical mathematics without OCC.
"""

import json
import math
from pathlib import Path

def compute_expected():
    l1, l2, l3, l4, l5 = 40.0, 30.0, 50.0, 30.0, 40.0
    w = 50.0
    t = 1.5
    r = 1.0
    R = r + t  # 2.5 mm

    # 1. Volume
    # V = sum(V_flanges) + 4 * V_sector
    # V_sector = (alpha / 2) * (R^2 - r^2) * w, with alpha = pi / 2
    v_flanges = (l1 + l2 + l3 + l4 + l5) * w * t
    v_sectors = 4.0 * ((math.pi / 2.0) * (R**2 - r**2) / 2.0 * w)
    volume_mm3 = v_flanges + v_sectors

    # 2. Bounding Box
    # Layout of stepped busbar:
    # - Flange 1: along X from 0 to l1 = 40.0, Z in [0, t] = [0, 1.5]
    # - Bend 1 (+90 deg up): X in [l1, l1 + R] = [40.0, 42.5], Z in [0, R] = [0, 2.5]
    # - Flange 2: along +Z, X in [l1 + r, l1 + R] = [41.0, 42.5], Z in [R, R + l2] = [2.5, 32.5]
    # - Bend 2 (-90 deg to +X): X in [l1 + r, l1 + r + R] = [41.0, 43.5], Z in [R + l2, R + l2 + R] = [32.5, 35.0]
    # - Flange 3: along +X, X in [l1 + r + R, l1 + r + R + l3] = [43.5, 93.5], Z in [R + l2 + r, R + l2 + R] = [33.5, 35.0]
    # - Bend 3 (+90 deg up): X in [93.5, 93.5 + R] = [93.5, 96.0], Z in [33.5, 33.5 + R] = [33.5, 36.0]
    # - Flange 4: along +Z, X in [94.5, 96.0], Z in [36.0, 36.0 + l4] = [36.0, 66.0]
    # - Bend 4 (-90 deg to +X): X in [94.5, 97.0], Z in [66.0, 68.5]
    # - Flange 5: along +X, X in [97.0, 97.0 + l5] = [97.0, 137.0], Z in [67.0, 68.5]
    # Total dx = l1 + (r + R) + l3 + (r + R) + l5 = 40 + 3.5 + 50 + 3.5 + 40 = 137.0 mm
    # Total dy = w = 50.0 mm
    # Total dz = R + l2 + (r + R) + l4 + R = 2.5 + 30 + 3.5 + 30 + 2.5 = 68.5 mm
    dx = l1 + 2.0 * (r + R) + l3 + l5
    dy = w
    dz = 2.0 * R + l2 + l4 + (r + R)  # 2.5 + 30 + 30 + 3.5 + 2.5 = 68.5 mm
    bbox_mm = [dx, dy, dz]

    # 3. Surface Area
    # Flange planar major faces: 2 for each of the 5 flanges
    a_flanges = 2.0 * (l1 + l2 + l3 + l4 + l5) * w
    # Cylindrical bend faces: 4 inner (radius r) + 4 outer (radius R)
    # Area = 4 * ((pi / 2) * r * w) + 4 * ((pi / 2) * R * w) = 2 * pi * (r + R) * w
    a_cyl = 4.0 * (math.pi / 2.0) * (r + R) * w
    # 2 end faces at the tips of Flange 1 and Flange 5: area = w * t each
    a_ends = 2.0 * (w * t)
    # 2 lateral side profile faces (at Y=0 and Y=w):
    # Cross-section area = sum(flange lengths) * t + 4 * ((pi / 4) * (R^2 - r^2))
    a_side_profile = (l1 + l2 + l3 + l4 + l5) * t + 4.0 * (math.pi / 4.0) * (R**2 - r**2)
    surface_area_mm2 = a_flanges + a_cyl + a_ends + 2.0 * a_side_profile

    # 4. Topology Counts
    # Planar faces:
    # - 2 for each of 5 flanges = 10
    # - 2 end cut faces = 2
    # - 2 lateral side faces (at Y=0 and Y=w) = 2
    # Total planar = 14
    # Cylindrical faces: 8 (4 inner concave, 4 outer convex)
    # Total faces = 22
    face_count = 22
    cylindrical_face_count = 8

    formulas = {
        "volume": "V = (l1 + l2 + l3 + l4 + l5) * w * t + 4 * (pi / 4) * ((r + t)**2 - r**2) * w",
        "bbox": "[dx, dy, dz] = [l1 + l3 + l5 + 2*(2*r + t), w, l2 + l4 + 2*(r + t) + (2*r + t)]",
        "surface_area": "A = 2 * (l1 + l2 + l3 + l4 + l5) * w + 2 * pi * (2*r + t) * w + 2 * (w * t) + 2 * ((l1 + l2 + l3 + l4 + l5) * t + pi * ((r + t)**2 - r**2))",
        "face_count": "22 faces: 10 flange major faces + 2 end faces + 2 side profile faces + 8 cylindrical faces",
        "cylindrical_face_count": "8 faces: 4 inner cylinders (radius r) + 4 outer cylinders (radius r + t)"
    }

    result = {
        "fixture_id": "four_bend",
        "volume_mm3": round(volume_mm3, 6),
        "bbox_mm": [round(x, 6) for x in bbox_mm],
        "face_count": face_count,
        "cylindrical_face_count": cylindrical_face_count,
        "surface_area_mm2": round(surface_area_mm2, 6),
        "formulas": formulas
    }

    out_path = Path(__file__).parent / "four_bend.expected.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"Wrote analytical expected metrics to {out_path}")
    return result

if __name__ == "__main__":
    compute_expected()
