"""Machine CAD role and transform regression without FreeCAD or Coin3D."""
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import patch
import json
import unittest

from freecad.bendseq.ui import coin3d_machine as machine


def vec(x,y,z):
    return NS(x=x,y=y,z=z)


def face(x,y,z,normal,width=1950):
    return NS(Surface=NS(TypeId="Part::GeomPlane"), CenterOfMass=vec(x,y,z),
              normalAt=lambda *_: vec(0,0,normal),
              BoundBox=NS(XLength=width), Area=width*60)


class FakeShape:
    def __init__(self, label, point, faces, bottom):
        self.label=label
        self.point=point
        self.Faces=faces
        self.BoundBox=NS(ZMin=bottom)
    def getElement(self,name):
        return self.Faces[int(name[4:])-1]
    def tessellate(self,_):
        return [self.point], [(0,0,0)]
    def isNull(self):
        return False


class FakeTransform:
    def __init__(self):
        self.translation=self
        self.value=None
    def setValue(self,*value):
        self.value=value


class MachineFrameTest(unittest.TestCase):
    def test_fixed_up_and_moving_z_share_bed_xy_even_with_legacy_sidecar(self):
        # Representative geometry from the uploaded assembly: z is narrower
        # and its mounting face is not at the same XY center as down.
        bed=FakeShape("down",vec(100,200,20),[face(100,200,20,1)],0)
        up=FakeShape("up",vec(100,400,500),[face(100,400,500,-1)],500)
        z=FakeShape("z",vec(90,190,300),[face(100,190,900,1),face(98,190,300,-1)],300)
        bodies=[NS(Name=name,Label=shape.label,Shape=shape,TypeId="PartDesign::Body")
                for name,shape in zip(("Body002","Body003","Body004"),(bed,up,z))]
        assembly=NS(Group=bodies)
        objects={body.Name:body for body in bodies}
        objects["Assembly"]=assembly
        @contextmanager
        def opened(_):
            yield NS(getObject=objects.get)
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            directory=root/"machine_frame"
            directory.mkdir()
            path=directory/"machine_frame.FCStd"
            path.touch()
            sidecar=path.with_suffix(".tooling.json")
            mount=dict(assembly_object="Assembly",bed_object="Body002",bed_face="Face1",
                       ram_object="Body003",ram_face="Face1",operator_y_sign=-1)
            sidecar.write_text(json.dumps({"visual_mount":mount}))
            with patch.object(machine,"_machine_document",opened), patch.object(machine,"_global_shape",lambda body:body.Shape):
                frame,beam,rest,width=machine.load_frame_meshes(root)
                self.assertEqual(frame[0],[(0,0,0),(200,0,480)])
                self.assertEqual(beam[0],[(-10,10,0)])
                self.assertEqual((rest,width),(280,1950))
                up.point=vec(100,400,800)
                changed,again,rest2,width2=machine.load_frame_meshes(root)
                self.assertEqual(changed[0][1],(200,0,780))
                self.assertEqual((again,rest2,width2),(beam,rest,width))
                mount.update(moving_object="Body004",moving_face="Face1")
                sidecar.write_text(json.dumps({"visual_mount":mount}))
                self.assertEqual(machine.load_frame_meshes(root)[1],beam)

    def test_ram_translation_is_absolute_and_only_z_changes(self):
        scene=machine.CoinMachineScene.__new__(machine.CoinMachineScene)
        scene.frame_mount=FakeTransform()
        scene.frame_mount.setValue(0,0,-80)
        scene.ram_motion=FakeTransform()
        scene.bed_z=-80
        scene.rest_z=300
        scene.punch_top=100
        scene.clamp_height=25
        for ram in (6.5,-4,6.5,-4):
            scene.last_ram_z=ram
            scene._position_ram()
            self.assertEqual(scene.ram_motion.value,(0,0,ram+125))
            self.assertEqual(scene.frame_mount.value,(0,0,-80))
        scene.clamp_height=30
        scene._position_ram()
        self.assertEqual(scene.ram_motion.value,(0,0,126))
        scene.punch_top=None
        scene._position_ram()
        self.assertEqual(scene.ram_motion.value,(0,0,220))

    def test_frame_key_changes_on_edit(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            directory=root/"machine_frame"
            directory.mkdir()
            path=directory/"machine_frame.FCStd"
            path.write_bytes(b"old")
            before=machine._frame_file_key(root)
            path.write_bytes(b"edited")
            self.assertNotEqual(before,machine._frame_file_key(root))


class ClampPreviewTest(unittest.TestCase):
    def test_project_dimensions_override_library_and_rebuild_on_edit(self):
        from dataclasses import replace
        from unittest.mock import Mock
        from freecad.bendseq.core.geometry_backend.io.clamp_validator import ClampConfig, save_clamp_config

        scene = machine.CoinMachineScene.__new__(machine.CoinMachineScene)
        scene.coin = NS(SO_SWITCH_NONE=-1, SO_SWITCH_ALL=-3, SoSeparator=Mock)
        scene.clamps = Mock()
        scene.clamp_config_key = None
        scene.clamp_key = None
        scene.clamp_spans = ()
        scene.clamp_config_error = ""
        scene.clamp_height, scene.clamp_depth = 25.0, 50.0
        scene.ram_motion = FakeTransform()
        scene.ram_mount_width = 500
        scene.last_ram_z = 6.5
        scene.punch_top = 100
        cfg = replace(ClampConfig.from_defaults(), width_mm=40.0, height_mm=35.0,
                      depth_mm=60.0, positions_mm=(-80.0, 80.0))
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            global_cfg = replace(cfg, width_mm=20.0, height_mm=25.0, depth_mm=50.0)
            save_clamp_config(root, global_cfg)
            with patch.object(machine, "_virtual_clamps") as mesh:
                self.assertEqual(scene.update_clamps(root, project_config=cfg), "")
                self.assertEqual(scene.clamp_spans, ((-100.0,-60.0),(60.0,100.0)))
                mesh.assert_called_once_with(scene.coin, scene.clamp_spans, 35.0,60.0)
                self.assertEqual(scene.ram_motion.value,(0,0,141.5))
                scene.update_clamps(root, project_config=cfg)
                self.assertEqual(mesh.call_count,1)
                edited=replace(cfg,width_mm=60.0,height_mm=45.0,depth_mm=70.0)
                scene.update_clamps(root, project_config=edited)
                self.assertEqual(scene.clamp_spans,((-110.0,-50.0),(50.0,110.0)))
                mesh.assert_called_with(scene.coin,scene.clamp_spans,45.0,70.0)
                self.assertEqual(scene.ram_motion.value,(0,0,151.5))
                self.assertEqual(mesh.call_count,2)
                # Another document without embedded clamps uses the library.
                scene.update_clamps(root)
                self.assertEqual(scene.clamp_spans,((-90.0,-70.0),(70.0,90.0)))
                mesh.assert_called_with(scene.coin,scene.clamp_spans,25.0,50.0)
                self.assertEqual(mesh.call_count,3)
                from freecad.bendseq.core.geometry_backend.io.clamp_validator import load_clamp_config
                self.assertEqual(load_clamp_config(root),global_cfg)

    def test_workspace_forwards_project_config(self):
        import ast
        root = Path(__file__).resolve().parents[1] / "freecad/bendseq"
        tree = ast.parse((root/"commands/pressbrake_2d.py").read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree) if isinstance(n,ast.Call)
                 and isinstance(n.func,ast.Attribute) and n.func.attr=="update_machine"]
        self.assertEqual(len(calls),1)
        keyword=next(k for k in calls[0].keywords if k.arg=="project_clamp_config")
        self.assertEqual(ast.unparse(keyword.value),"self._project_clamp_config")
