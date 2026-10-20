"""KL-8c-fix: place_part_on_tool_station must reject candidates colliding with punch."""
import sys, os, unittest, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.Physics.collision_detector import CollisionDetector
from smbs_core.Physics.tooling_assembly import ToolingKind
from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
from tests.test_technological_unfold_single_bend import create_l_bracket_shape


class TestPlacePartAvoidsPunch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.punch_path = str(ROOT / "toollibrary/punch/puancon.FCStd")
        cls.die_path = str(ROOT / "toollibrary/die/matrica.FCStd")
        cls.backend = PythonOCCBackend()

    def _detector_with_real_tooling(self):
        machine = PressBrakeMachine(
            punch_path=self.punch_path, die_path=self.die_path,
            punch_target_object_name="Body", die_target_object_name="Body")
        return CollisionDetector(machine, self.backend)

    def test_place_part_fixture_l_no_punch_collision(self):
        """After fix, place_part_on_tool_station must not return a candidate that
        intersects punch. Verified on fixture_l at initial APPROACH position."""
        detector = self._detector_with_real_tooling()
        l_shape = create_l_bracket_shape(
            angle_deg=90.0, radius=1.0, thickness=1.5,
            length=50.0, l1=95.5, l2=47.5)
        bend_info = {"origin": (95.5, 0.0, 2.5), "axis": (0.0, 1.0, 0.0), "angle": 90.0}
        placed = detector.place_part_on_tool_station(l_shape, bend_info)
        punch = detector.assembly.get_world_shape(ToolingKind.PUNCH)
        res = detector._check_intersection(placed, punch, "Part", "Punch")
        self.assertTrue(res.is_valid,
                        f"Placed shape must not intersect punch, got: {res.message}")

    def test_place_part_at_trajectory_minus10_no_punch_collision(self):
        """REGRESSION test for original bug: at trajectory angle -10°,
        place_part_on_tool_station must not return punch-intersecting candidate.
        Before fix: returned candidate with vol=1959 mm³.
        After fix: punch+die both checked, clean candidate chosen."""
        from smbs_core.Geometry.feature_extractor import FeatureExtractor
        from smbs_core.Core.panel_graph import PanelGraph
        from smbs_core.geometry_backend.panel_face_resolver import resolve_panel_faces

        detector = self._detector_with_real_tooling()
        l_shape = create_l_bracket_shape(
            angle_deg=90.0, radius=1.0, thickness=1.5,
            length=50.0, l1=95.5, l2=47.5)
        feats = FeatureExtractor(self.backend).extract_features(l_shape)
        b_id = list(feats['bends'].keys())[0]
        b_info = feats['bends'][b_id]

        pg = PanelGraph()
        pg.add_bend_connection(b_id, b_info['parent_panel_id'], b_info['child_panel_id'])
        canonical_payload = {
            'thickness_mm': {'value': 1.5},
            'panels': {pid: {'normal': pd['normal'], 'centroid': pd['centroid']}
                       for pid, pd in feats['panels'].items()},
            'bends': {b_id: {'axis_point': b_info['origin'],
                             'child_panel_id': b_info['child_panel_id']}},
        }
        resolve_panel_faces(l_shape, pg, canonical_payload)
        moving_panels = pg.moving_subtree(b_id)

        rotated = self.backend.rotate_subtree(
            l_shape, b_info['origin'], b_info['axis'], -10.0, moving_panels, pg)

        placed = detector.place_part_on_tool_station(rotated, b_info)
        punch = detector.assembly.get_world_shape(ToolingKind.PUNCH)
        res = detector._check_intersection(placed, punch, "Part", "Punch")
        self.assertTrue(res.is_valid,
                        f"At trajectory -10°, placed shape must not intersect punch, got: {res.message}")


if __name__ == "__main__":
    unittest.main()
