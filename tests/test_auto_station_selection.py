import unittest
from pathlib import Path
from freecad.bendseq.core.geometry_backend.io.clamp_validator import ClampConfig
from freecad.bendseq.core.geometry_backend.io.tooling_catalog import ToolDescriptor, ToolingCatalog
from freecad.bendseq.core.kinematics2d.auto_station_selection import plan_stations
from freecad.bendseq.core.kinematics2d.auto_tool_selection import BendRequirement
from freecad.bendseq.core.kinematics2d.station_layout import validate_station_layout
from freecad.bendseq.core.kinematics2d.bending_technology import Material, TechnologyInputs


def tool(kind,name,length,quantity,**metadata):
    path = Path(name+".FCStd")
    return ToolDescriptor(name,kind,path,path.with_suffix(".tooling.json"),True,
                          dict(segments=[dict(length_mm=length,quantity=quantity)],**metadata))


class AutoStationTests(unittest.TestCase):
    def setUp(self):
        self.clamps = ClampConfig(20,0,(-50,50),0.4,0.1,1,repositionable=False)
        self.p = tool("punch","P",50,2)
        self.d = tool("die","D",50,2,v_width_mm=12)
        self.catalog = ToolingCatalog(Path("."),(self.p,),(self.d,))

    def test_reuses_one_station_and_assigns_stage_ids(self):
        r = plan_stations([BendRequirement("B01.P",50),BendRequirement("B01.F",50)],
                          self.catalog,self.clamps,(-100,100),10)
        self.assertEqual(r.status,"DRAFT_UNVERIFIED")
        self.assertEqual(len(r.setup.stations),1)
        self.assertEqual(set(dict(r.setup.assignments)),{"B01.P","B01.F"})

    def test_force_and_length_require_two_distinct_dies(self):
        # The wide die has only 50 mm of stock; the long narrow die
        # can form the thin long bend but exceeds force on the thick short bend.
        clamps = ClampConfig(40,0,(-100,-50,50,100),0.4,0.1,1,
                             repositionable=False,joints_must_be_on_clamps=False)
        p = tool("punch","P",50,3)
        narrow = tool("die","N",100,1,v_width_mm=8)
        wide = tool("die","W",50,1,v_width_mm=32)
        catalog = ToolingCatalog(Path("."),(p,),(narrow,wide))
        bends = [BendRequirement("B01",100,30,1,90),BendRequirement("B02",50,40,5,90)]
        r = plan_stations(bends,catalog,clamps,(-150,150),10,
                          technology=TechnologyInputs(Material("Test",450,"certificate"),50))
        self.assertEqual(r.status,"DRAFT_UNVERIFIED")
        self.assertEqual(len(r.setup.stations),2)
        self.assertEqual(r.setup.assigned_station("B01").die_id,"N")
        self.assertEqual(r.setup.assigned_station("B02").die_id,"W")
        stock = {(t.kind,t.tool_id):t.metadata["segments"] for t in catalog.all_tools()}
        validate_station_layout(r.setup,stock,clamps)

    def test_cancel_and_no_stock_do_not_return_partial_draft(self):
        req = [BendRequirement("B01",50)]
        r = plan_stations(req,self.catalog,self.clamps,(-100,100),10,cancelled=lambda:True)
        self.assertIsNone(r.setup)
        self.assertEqual(r.status,"CANCELLED")
        r = plan_stations([BendRequirement("B01",500)],self.catalog,self.clamps,(-100,100),10)
        self.assertIsNone(r.setup)
