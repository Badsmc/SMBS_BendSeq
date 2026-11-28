import unittest
from freecad.bendseq.core.kinematics2d.bending_technology import (
    Material, TechnologyInputs, estimate_force_kn, assess_technology)
from freecad.bendseq.core.kinematics2d.auto_tool_selection import BendRequirement


class BendingTechnologyTests(unittest.TestCase):
    def setUp(self):
        self.material = Material("Test steel", 450, "test certificate")
        self.bends = [BendRequirement("B01", 500, 30, 3, 90),
                      BendRequirement("B02", 500, 30, 3, -90)]
        self.rating = {"max_load_kn_per_m": 300, "load_rating_source": "test catalog"}

    def test_formula(self):
        self.assertAlmostEqual(estimate_force_kn(450, 3, 1000, 24), 239.625)
        with self.assertRaises(ValueError):
            estimate_force_kn(450, 3, 1000, 0)

    def test_group_force_is_sum_not_span(self):
        inputs = TechnologyInputs(self.material, 200)
        individual = assess_technology(self.bends, 24, self.rating, self.rating, inputs)
        self.assertFalse(individual["blocked"])
        shared = assess_technology(self.bends, 24, self.rating, self.rating, inputs,
                                   groups=[("B01", "B02")])
        self.assertAlmostEqual(shared["strokes"][0]["total_reference_kn"], 239.625)
        self.assertEqual(shared["blocked"], ["ESTIMATED_MACHINE_OVERLOAD: B01,B02"])

    def test_rated_tool_and_unknown_units(self):
        low = {"max_load_kn_per_m": 100, "load_rating_source": "test catalog"}
        report = assess_technology(self.bends[:1], 24, low, {"maximum_load": 1},
                                   TechnologyInputs(self.material))
        self.assertIn("ESTIMATED_PUNCH_OVERLOAD: B01", report["blocked"])
        self.assertIn("DIE_LOAD_RATING_UNKNOWN", report["warnings"])

    def test_unknown_material_and_non90_do_not_infer_capacity(self):
        report = assess_technology(self.bends, 24, {}, {}, TechnologyInputs())
        self.assertIn("MATERIAL_UNKNOWN", report["warnings"])
        self.assertIsNone(report["strokes"][0]["total_reference_kn"])
        other = [BendRequirement("B03", 1000, 30, 3, 45)]
        report = assess_technology(other, 24, self.rating, self.rating,
                                   TechnologyInputs(self.material, 1))
        self.assertFalse(report["blocked"])
        self.assertEqual(report["strokes"][0]["angle_scope"], "REFERENCE_ONLY")

    def test_material_requires_source(self):
        with self.assertRaises(ValueError):
            Material("Steel", 450, "")
