"""Strict advisory FAST tool-penetration filtering without OCC / FreeCAD.

Only clear-cut tool/material interior overlap observed at sampled Stage B
machine cutting-plane poses can temporarily suppress an EXACT search edge.
"""
from __future__ import annotations

from types import SimpleNamespace
import unittest

from smbs_core.kinematics2d.fast_collision_gate import (
    coarse_tool_gate, rerun_fast_avoiding_tool_collisions,
)
from smbs_core.kinematics2d.fast_bfm_beam_search import FastCandidate
from smbs_core.kinematics2d.fast_shortlist_screening import ShortlistCandidateCheck


def fake_screen(*entries):
    return SimpleNamespace(checks=entries, ranked_indices=tuple(range(len(entries))))


def hit(op="B01", tool="PUNCH", phase="BENDING",
        component="panel:P11", contact="proper_material_boundary_crossing",
        reverse=False, mask=0, status="POTENTIAL_2D_INTERFERENCE"):
    return ShortlistCandidateCheck(
        (op,), status, 1, 0,
        "{}:tool={};phase={};component={};contact={};"
        "2d_section_finding_requires_3d_validation".format(
            op, tool, phase, component, contact,
        ),
        0.05, (mask, op, reverse),
    )


class CoarseToolGateTests(unittest.TestCase):
    def test_punch_bending_and_die_approach_proven_interiors_are_gated(self):
        result = coarse_tool_gate(fake_screen(
            hit("B01", "PUNCH", "BENDING", "panel:P11",
                "proper_material_boundary_crossing", False, 5),
            hit("B02", "DIE", "APPROACH", "panel:P09",
                "sheet_in_tool_material", True, 7),
        ))
        self.assertEqual(result.forbidden_transitions, (
            (5, "B01", False), (7, "B02", True),
        ))
        self.assertEqual(result.observed_obstructions[0]["component"], "panel:P11")
        self.assertEqual(result.observed_obstructions[1]["tool"], "DIE")
        self.assertTrue(result.to_dict()["provisional_only"])

    def test_actual_smbs_component_id_p09_is_accepted(self):
        # SMBS Stage B machine_section_3d reports other rigid panels
        # as bare IDs (P09), not necessarily the prefixed panel:P09.
        gate = coarse_tool_gate(fake_screen(
            hit("B04", "PUNCH", "BENDING", "P09",
                "proper_material_boundary_crossing", False, 8),
        ))
        self.assertEqual(gate.forbidden_transitions, ((8,"B04",False),))
        self.assertEqual(gate.observed_obstructions[0]["component"],"P09")

    def test_retract_touch_uncertain_backgauge_or_active_zone_is_not_gated(self):
        checks = (
            hit("B01", phase="RETRACT"),
            hit("B02", tool="BACKGAUGE"),
            hit("B03", contact="boundaries_within_tolerance"),
            hit("B04", component="zone_polygon"),
            hit("B05", status="BUDGET_EXCEEDED"),
            ShortlistCandidateCheck(
                ("B06",), "POTENTIAL_2D_INTERFERENCE", 1, 0,
                "B06:2d_section_finding_requires_3d_validation:"
                "BENDING:panel:P11:proper_material_boundary_crossing",
                0.1, (0, "B06", False),
            ),
        )
        self.assertEqual(coarse_tool_gate(fake_screen(*checks))
                         .forbidden_transitions, ())

    def test_exact_mask_orientation_and_op_not_global_bend_ban(self):
        result = coarse_tool_gate(fake_screen(
            hit(op="B01", reverse=True, mask=17),
            hit(op="B01", reverse=True, mask=17),
        ))
        self.assertEqual(result.forbidden_transitions, ((17,"B01",True),))
        self.assertNotIn((17,"B01",False), result.forbidden_transitions)
        self.assertNotIn((0,"B01",True), result.forbidden_transitions)

    def test_global_rerun_forwards_strict_gate_and_skips_duplicate_orders(self):
        baseline = FastCandidate(
            ("B01","B02"),(("B01",False),("B02",False)),12.0,0.0,
        )
        new = FastCandidate(
            ("B02","B01"),(("B02",True),("B01",False)),19.0,0.0,
        )
        called = []

        def search(state,operations,**kwargs):
            called.append(kwargs)
            return SimpleNamespace(
                candidates=(baseline,new),
                coarse_2d_pruned_expansions=7,
                explored_states=43,
                status="CANDIDATES_WITH_COARSE_2D_GATE_UNVERIFIED",
            )

        result = rerun_fast_avoiding_tool_collisions(
            object(), (), (baseline,), {}, fake_screen(hit()),
            search_fn=search,
        )
        self.assertEqual(len(called),1)
        self.assertEqual(called[0]["forbidden_transitions"],((0,"B01",False),))
        self.assertEqual(len(result.candidates),1)
        self.assertEqual(result.candidates[0].operation_ids,("B02","B01"))
        self.assertEqual(result.pruned_expansions,7)
        self.assertIn("UNVERIFIED",result.status)

    def test_no_strong_evidence_never_launches_strict_rerun(self):
        result = rerun_fast_avoiding_tool_collisions(
            object(), (), (), {}, fake_screen(hit(phase="RETRACT")),
            search_fn=lambda *args,**kwargs: self.fail("No hard findings"),
        )
        self.assertEqual(result.status,"NO_STRONG_2D_TOOL_OBSTRUCTIONS")


if __name__=="__main__":
    unittest.main()
