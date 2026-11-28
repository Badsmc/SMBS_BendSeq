import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from freecad.bendseq.core.kinematics2d.mesh_sequence_screen import screen_mesh_sequence
from freecad.bendseq.core.kinematics2d.press_cycle import CyclePhase
from freecad.bendseq.core.kinematics2d.station_mesh_screen import screen_station_sequence

MODULE = "freecad.bendseq.core.kinematics2d.mesh_sequence_screen"
ADAPTER = "freecad.bendseq.core.kinematics2d.station_mesh_screen"


class StationMeshTests(unittest.TestCase):
    def test_neighbor_hit_is_not_active_contact_exemption(self):
        triangle = ((0,0,0),(1,0,0),(0,1,0))
        state = Mock()
        state.bends = {"B": NS(axis_xyz=((0,0,0),(10,0,0)))}
        state.angles = {"B":0}
        seq = Mock(fold_state=state, active_operation_id="B")
        cycle = Mock(active_bend_id="B", current_angle_deg=0, target_angle_deg=90,
                     upper_z_mm=10,contact_z_mm=2,bottom_z_mm=-2,
                     coupled_kinematics=NS(stroke_for_angle=lambda a:a/30))
        cycle.current_workpiece_placement_3d.return_value = Mock(rotation=(),translation=())
        active = NS(machine_shape="active")
        neighbor = NS(machine_shape="neighbor")
        mounted = [("S1","punch",active,(triangle,)),("S1","die",active,(triangle,)),
                   ("S2","punch",neighbor,(triangle,)),("S2","die",neighbor,(triangle,))]
        preview = NS(sequence=seq,tooling=Mock(),config=None,operation_setup=((Mock(),-50),),
                     loading_orientation_by_bend={},assisted_retract_preview_bends=set())
        # Only bending is a real forming contact sliver. Test neighbor even
        # when the same sliver classifier would excuse the active punch.
        calls = []
        def confirm(session, state, component, pose, shape, prisms, budget, **kwargs):
            calls.append((shape,cycle.phase,kwargs["end_zone_length"]))
            volume = 0.0005 if cycle.phase is CyclePhase.BENDING and shape in ("active","neighbor") else 0
            return volume, {"zmax":0.0005,"zmin":0},False,None,shape,None
        with patch(MODULE+".PressCycleController",return_value=cycle) as build, \
             patch(MODULE+"._panel_meshes",return_value={}), \
             patch(MODULE+"._sheet_triangles",return_value=[("B",triangle)]), \
             patch(MODULE+"._crosses_tool",return_value=True), \
             patch(MODULE+"._confirm_crossing",side_effect=confirm), \
             patch("freecad.bendseq.core.kinematics2d.native_tool_loader.set_punch_ram_z",side_effect=lambda t,z:t):
            report = screen_mesh_sequence(NS(),preview,("B",),mounted_meshes=mounted,station_ids=("S1",))
        self.assertEqual(report["reason"],"")
        self.assertEqual(report["completed"],["B"])
        self.assertEqual(report["samples"],15)
        self.assertTrue(any(h["station"]=="S2" and h["tool"]=="punch" and h["neighboring_station"]
                            for h in report["findings"]))
        self.assertFalse(any(h["station"]=="S1" and h["tool"]=="punch" for h in report["findings"]))
        self.assertTrue(all(c[2] is None for c in calls))
        self.assertEqual(build.call_args.kwargs["operation_setup"][0][1],-50)

    def test_missing_installed_tool_fails_closed(self):
        from freecad.bendseq.core.kinematics2d.bending_stations import BendingStation, StationSetup
        s = BendingStation("S1","S1","P","D",0,((-10,10),),((-10,10),))
        setup = StationSetup((-50,50),(s,),assignments=(("B","S1"),))
        session = NS(controller=NS(operation_steps=(NS(operation_id="B"),)),panel_vertices_xy={})
        with patch(ADAPTER+".validate_station_layout"), \
             patch(ADAPTER+".build_station_cycle",return_value=(NS(operation_setup=((None,0),)),("S1",))), \
             patch(ADAPTER+".mounted_station_meshes",return_value=()), \
             patch(ADAPTER+".screen_mesh_sequence") as screen:
            result = screen_station_sequence(session,setup,{}, {},None,{},None)
        self.assertEqual(result["pending"],["B"])
        self.assertIn("Installed meshes",result["reason"])
        screen.assert_not_called()
