import unittest
from pathlib import Path
from unittest.mock import patch
from freecad.bendseq.core.geometry_backend.io.tooling_catalog import ToolDescriptor, ToolingCatalog
from freecad.bendseq.core.kinematics2d.auto_tool_selection import BendRequirement, shortlist


def tool(kind, name, length=100, quantity=2, **metadata):
    path = Path(name + ".FCStd")
    return ToolDescriptor(name, kind, path, path.with_suffix(".tooling.json"), True,
                          dict(segments=[dict(length_mm=length, quantity=quantity)], **metadata))


def catalog(punches, dies):
    return ToolingCatalog(Path("."), tuple(punches), tuple(dies))


def plan(*args, **kwargs):
    return dict(status="ASSEMBLY_CANDIDATE_UNVERIFIED", punch_segments=[{}],
                die_segments=[{}], punch_total_mm=200, die_total_mm=200)


class AutoToolSelectionTests(unittest.TestCase):
    def test_inventory_rejects_short_tools_and_uses_shared_span(self):
        c = catalog([tool("punch", "short", 100, 1), tool("punch", "long")],
                    [tool("die", "die", v_width_mm=12)])
        with patch("freecad.bendseq.core.kinematics2d.auto_tool_selection.plan_pair_for_bend",
                   side_effect=plan) as mock:
            r = shortlist(c, [BendRequirement("B01", 80, 20)], object(), required_span_mm=180)
        self.assertEqual([x.punch_id for x in r.candidates], ["long"])
        self.assertEqual(r.rejected, (("short", "", "INSUFFICIENT_STOCK"),))
        self.assertEqual(mock.call_args.args[2], 180)
        self.assertTrue(mock.call_args.kwargs["full_coverage"])
        self.assertEqual(r.candidates[0].status, "CANDIDATE_UNVERIFIED")

    def test_nominal_flange_warning_ranks_lower(self):
        c = catalog([tool("punch", "p")], [tool("die", "wide", v_width_mm=30),
                                          tool("die", "narrow", v_width_mm=12)])
        with patch("freecad.bendseq.core.kinematics2d.auto_tool_selection.plan_pair_for_bend",
                   side_effect=plan):
            r = shortlist(c, [BendRequirement("B01", 100, 20)], object())
        self.assertEqual(r.candidates[0].die_id, "narrow")
        self.assertIn("FLANGE_BELOW_NOMINAL_V: B01", r.candidates[1].warnings)

    def test_no_plan_or_cancel_is_not_feasibility(self):
        c = catalog([tool("punch", "p")], [tool("die", "d")])
        with patch("freecad.bendseq.core.kinematics2d.auto_tool_selection.plan_pair_for_bend",
                   return_value=dict(status="NO_CLAMP_COMPATIBLE_ASSEMBLY")):
            r = shortlist(c, [BendRequirement("B01", 100)], object())
        self.assertFalse(r.candidates)
        self.assertEqual(r.rejected[0][2], "NO_CLAMP_COMPATIBLE_ASSEMBLY")
        stopped = shortlist(c, [BendRequirement("B01", 100)], object(), cancelled=lambda: True)
        self.assertFalse(stopped.complete)
        self.assertEqual(stopped.examined, 0)


    def test_technology_excludes_estimated_overload_before_planner(self):
        from freecad.bendseq.core.kinematics2d.bending_technology import Material, TechnologyInputs
        c = catalog([tool("punch", "p")], [tool("die", "narrow", v_width_mm=8),
                                         tool("die", "wide", v_width_mm=24)])
        events = []
        with patch("freecad.bendseq.core.kinematics2d.auto_tool_selection.plan_pair_for_bend",
                   side_effect=plan) as planner:
            r = shortlist(c, [BendRequirement("B01", 100, 40, 3, 90)], object(),
                          technology_inputs=TechnologyInputs(Material("Test", 450, "test"), 50),
                          trace=lambda event, **data: events.append((event, data)))
        self.assertEqual([x.die_id for x in r.candidates], ["wide"])
        self.assertEqual(planner.call_count, 1)
        self.assertIn("ESTIMATED_MACHINE_OVERLOAD", r.rejected[0][2])
        self.assertEqual(sum(e == "TECHNOLOGY_PAIR" for e, _ in events), 2)
        self.assertAlmostEqual(r.candidates[0].technology["strokes"][0]["total_reference_kn"], 23.9625)
