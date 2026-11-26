"""Headless lifecycle regression for the actual TaskPanel adapter."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/"freecad_smbs/commands/pressbrake_2d.py").read_text(encoding="utf-8")


class Signal:
    def connect(self,fn):
        self.fn=fn


class Widget:
    def __init__(self):
        self.destroyed=Signal()
        self.parent=None
        self.hidden=False
    def installEventFilter(self,guard):
        self.guard=guard
    def parentWidget(self):
        return self.parent
    def layout(self):
        return None
    def setParent(self,parent):
        self.parent=parent
    def hide(self):
        self.hidden=True
    def show(self):
        self.hidden=False


class TaskLifecycleTest(unittest.TestCase):
    def setUp(self):
        cls=next(n for n in ast.parse(SOURCE).body if isinstance(n,ast.ClassDef) and n.name=="_SMBSNativeTaskPanel")
        class Object:
            def __init__(self,parent):
                self.parent=parent
        self.events=NS(Hide=1,DeferredDelete=2,Show=3)
        ns={"_qt":lambda:(NS(QObject=Object,QEvent=self.events),None,None),
            "_desktop_debug_event":lambda *a,**k:None}
        exec(compile(ast.Module(body=[cls],type_ignores=[]),"<adapter>","exec"),ns)
        self.ws=NS(_qt_is_valid=lambda w:w is not None,_closing=False)
        self.form=Widget()
        self.children=[]
        for name in ("_task_stack","status","task_progress","_task_status_chip",
                     "_task_progress_caption","_task_result_icons","show_task_panel_btn"):
            w=Widget()
            w.parent=self.form
            setattr(self.ws,name,w)
            if name!="show_task_panel_btn":
                self.children.append(w)
        self.panel=ns["_SMBSNativeTaskPanel"](self.ws,self.form)
        self.ws._native_task_panel=self.panel
        self.ws.show_task_panel_btn.hide()

    def send(self,kind):
        self.form.guard.eventFilter(self.form,NS(type=lambda:kind))

    def test_minimize_and_restore_keep_every_control(self):
        self.send(self.events.Hide)
        self.send(self.events.Show)
        self.assertTrue(self.panel._attached)
        self.assertTrue(all(w.parent is self.form for w in self.children))
        self.assertTrue(self.ws.show_task_panel_btn.hidden)

    def test_close_or_deferred_delete_detaches_once(self):
        self.send(self.events.DeferredDelete)
        self.assertFalse(self.panel._attached)
        self.assertTrue(all(w.parent is self.ws for w in self.children))
        self.assertIsNone(self.ws._native_task_panel)
        self.assertFalse(self.ws.show_task_panel_btn.hidden)
        self.assertTrue(self.panel.reject())

    def test_synchronous_destroy_rescues_controls_without_layout_access(self):
        def fail():
            raise AssertionError("dying parent accessed")
        self.form.layout=fail
        self.form.destroyed.fn()
        self.assertTrue(all(w.parent is self.ws for w in self.children))

    def test_other_workbench_task_not_owned(self):
        mine=NS(isAncestorOf=lambda form:form is self.form)
        other=NS(isAncestorOf=lambda form:False)
        for box,expected in ((mine,True),(other,False)):
            control=NS(activeTaskDialog=lambda:NS(getDialogContent=lambda:[box]))
            self.assertEqual(self.panel.owns_active_dialog(control),expected)
