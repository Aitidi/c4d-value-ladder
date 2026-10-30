"""Focused numeric probe regression checks; run inside Cinema 4D.

Uses disposable documents, does not start a hook or inject UI input.
"""
import importlib.machinery
import importlib.util
import os
import unittest
from unittest import mock

import c4d


def _load_plugin():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '..', 'plugin', 'ValueLadder', 'ValueLadder.pyp')
    loader = importlib.machinery.SourceFileLoader('valueladder_focus_checks', path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    plugin = importlib.util.module_from_spec(spec)
    loader.exec_module(plugin)
    plugin.ENABLE_PROBE_FALLBACK = True   # the probe checks exercise the fallback
    return plugin


def main():
    plugin = _load_plugin()

    class FocusChecks(unittest.TestCase):
        def setUp(self):
            self.doc = c4d.documents.BaseDocument()
            self.node = c4d.BaseObject(c4d.Ocube)
            self.doc.InsertObject(self.node)
            self.ids = []
            for name, dtype, value in (('Real', c4d.DTYPE_REAL, 12.5),
                                       ('Integer', c4d.DTYPE_LONG, 7),
                                       ('Upper bound', c4d.DTYPE_REAL, 1.0)):
                data = c4d.GetCustomDataTypeDefault(dtype)
                data[c4d.DESC_NAME] = name
                if name == 'Upper bound':
                    data[c4d.DESC_MIN], data[c4d.DESC_MAX] = 0.0, 1.0
                descid = self.node.AddUserData(data)
                self.node[descid] = value
                self.ids.append(descid)
            self.doc.FlushUndoBuffer()

        def tearDown(self):
            c4d.documents.KillDocument(self.doc)

        def controller(self):
            ctrl = plugin.Controller(lambda: None)
            ctrl.doc = self.doc
            ctrl.cands = [self.node]
            ctrl.before = [plugin._snapshot(self.node)]
            ctrl.noisy = [set()]
            ctrl.undo_before = self.doc.GetUndoPtr()
            state = self.doc.FindUndoPtr(self.node, c4d.UNDOTYPE_CHANGE)
            ctrl.undo_marks = [(self.node, state is not None)]
            ctrl.phase = plugin.IDENTIFYING
            ctrl.focus_probe = True
            ctrl.focus_attempt = 1
            ctrl.resume_pt = (0, 0)
            ctrl._redraw = lambda **kwargs: None
            return ctrl

        def native_change(self, changes):
            self.doc.StartUndo()
            self.doc.AddUndo(c4d.UNDOTYPE_CHANGE, self.node)
            for index, value in changes:
                self.node[self.ids[index]] = value
            self.doc.EndUndo()

        def identify(self, ctrl):
            with mock.patch.object(plugin, '_cursor_pos', return_value=ctrl.resume_pt), \
                    mock.patch.object(c4d.documents, 'GetActiveDocument', return_value=self.doc):
                ctrl._finish_identify()

        def value(self, index=0):
            # Undo swaps the original object into the redo buffer.
            return self.doc.GetFirstObject()[self.ids[index]]

        def test_restore_precedes_first_ladder_write(self):
            ctrl = self.controller()
            self.native_change([(0, 12.501)])
            seen = []
            ctrl._update = lambda *args: seen.append(self.value())
            self.identify(ctrl)
            self.assertEqual(seen, [12.5])
            self.assertEqual(ctrl.phase, plugin.LADDER)
            self.assertTrue(ctrl.scrub_undo)

        def test_drag_single_undo_and_redo(self):
            ctrl = self.controller()
            self.native_change([(0, 12.501)])
            ctrl.out_dx = plugin.PX_PER_STEP * 2
            self.identify(ctrl)
            self.assertEqual(self.value(), 14.5)
            ctrl._finish()
            self.doc.DoUndo(False)
            self.assertEqual(self.value(), 12.5)
            self.doc.DoRedo()
            self.assertEqual(self.value(), 14.5)

        def test_cancel_keeps_previous_undo(self):
            self.native_change([(0, 20.0)])
            ctrl = self.controller()
            self.native_change([(0, 20.001)])
            self.identify(ctrl)
            ctrl._cancel()
            self.assertEqual(self.value(), 20.0)
            self.doc.DoUndo(False)
            self.assertEqual(self.value(), 20.0)  # unchanged native probe entry
            self.doc.DoUndo(False)
            self.assertEqual(self.value(), 12.5)

        def test_release_without_motion_removes_probe(self):
            ctrl = self.controller()
            self.native_change([(0, 12.501)])
            ctrl.released = True
            self.identify(ctrl)
            self.assertEqual(ctrl.phase, plugin.IDLE)
            self.assertEqual(self.value(), 12.5)
            self.assertIsNone(self.doc.FindUndoPtr(self.doc.GetFirstObject(), c4d.UNDOTYPE_CHANGE))

        def test_integer(self):
            ctrl = self.controller()
            self.native_change([(1, 8)])
            ctrl.out_dx = plugin.PX_PER_STEP
            self.identify(ctrl)
            self.assertEqual(self.value(1), 8)
            self.assertTrue(ctrl.param.is_int)
            self.assertEqual(ctrl.last_rung, plugin.INT_LAST_RUNG)
            self.assertEqual(ctrl.rungs, plugin.RUNGS[:plugin.INT_LAST_RUNG + 1])   # fractions hidden
            self.assertEqual(ctrl.rungs[-1], 1.0)

        def test_upper_bound_restores_then_clamps(self):
            ctrl = self.controller()
            self.native_change([(2, 0.999)])
            ctrl.out_dx = plugin.PX_PER_STEP * 5
            self.identify(ctrl)
            self.assertEqual(self.value(2), 1.0)
            ctrl._finish()
            self.assertIsNone(self.doc.FindUndoPtr(self.doc.GetFirstObject(), c4d.UNDOTYPE_CHANGE))

        def test_ambiguous_changes_rollback_without_guessing(self):
            ctrl = self.controller()
            self.native_change([(0, 12.501), (1, 8)])
            self.identify(ctrl)
            self.assertEqual(ctrl.phase, plugin.IDLE)
            self.assertEqual(self.value(), 12.5)
            self.assertEqual(self.value(1), 7)
            self.assertIsNone(ctrl.param)

        def test_ambiguous_without_undo_restores_known_changes(self):
            ctrl = self.controller()
            self.node[self.ids[0]] = 12.501
            self.node[self.ids[1]] = 8
            self.identify(ctrl)
            self.assertEqual(ctrl.phase, plugin.IDLE)
            self.assertEqual(self.value(), 12.5)
            self.assertEqual(self.value(1), 7)

        def test_unchanged_retries_for_rounding_and_limits(self):
            ctrl = self.controller()
            ctrl._launch_focus_probe = mock.Mock()
            self.identify(ctrl)
            ctrl._launch_focus_probe.assert_called_once_with()
            self.assertIsNone(ctrl.param)

        def test_unchanged_new_undo_is_removed(self):
            ctrl = self.controller()
            self.native_change([(0, 12.5)])
            ctrl._launch_focus_probe = mock.Mock()
            self.identify(ctrl)
            self.assertEqual(ctrl.phase, plugin.IDENTIFYING)
            ctrl._launch_focus_probe.assert_called_once_with()
            self.assertEqual(self.value(), 12.5)

        def run_probe_plan(self, node_getter, pid, kind, comp=None, record_noops=False):
            ctrl = self.controller()
            ctrl.cands = [node_getter()]
            ctrl.before = [plugin._snapshot(ctrl.cands[0])]
            ctrl.noisy = [set()]
            ctrl.undo_marks = [(ctrl.cands[0], False)]
            ctrl.focus_attempt = 0
            ctrl.capturing = True
            ctrl.input_root = 123
            ctrl._input_ready = lambda: True
            ctrl._hide_cursor = mock.Mock()
            ctrl._window_title = lambda point: 'Cinema 4D'
            ctrl._floating = lambda point: False
            steps = []

            def native_input(mode, amount):
                steps.append((mode, amount))
                node = node_getter()  # a no-op undo can replace the old handle
                param = plugin.Param(node, pid, kind, comp)
                original = param.get(node)
                delta = amount if mode == 'small' else (1 if amount > 0 else -1)
                target = original + delta / param.scale
                if param.vmin is not None:
                    target = max(param.vmin, target)
                if param.vmax is not None:
                    target = min(param.vmax, target)
                if param.is_int:
                    target = int(round(target))
                if target != original or record_noops:
                    self.doc.StartUndo()
                    self.doc.AddUndo(c4d.UNDOTYPE_CHANGE, node)
                    param.set(node, target)
                    self.doc.EndUndo()

            class InlineWorker:
                def __init__(self, target, **kwargs):
                    self.target = target
                def start(self):
                    self.target()

            with mock.patch.object(plugin, '_send_probe_step', side_effect=native_input), \
                    mock.patch.object(plugin, '_send_key'), mock.patch.object(plugin, '_send_mouse'), \
                    mock.patch.object(plugin, '_move_to'), mock.patch.object(plugin, '_cursor_pos', return_value=(0, 0)), \
                    mock.patch.object(plugin.user32, 'GetForegroundWindow', return_value=123), \
                    mock.patch.object(plugin.user32, 'GetAsyncKeyState', return_value=0), \
                    mock.patch.object(plugin.threading, 'Thread', InlineWorker), \
                    mock.patch.object(plugin.time, 'sleep'), \
                    mock.patch.object(plugin, '_candidates', side_effect=lambda *args, **kwargs: [node_getter()]), \
                    mock.patch.object(c4d.documents, 'GetActiveDocument', return_value=self.doc):
                ctrl._launch_focus_probe()
                for _ in range(len(plugin.FOCUS_PROBES) + 1):
                    if ctrl.phase != plugin.IDENTIFYING:
                        break
                    ctrl.pump()
            self.assertEqual(ctrl.phase, plugin.LADDER)
            return ctrl, steps

        def test_small_scale_probe_succeeds_without_native_fallback(self):
            ctrl, steps = self.run_probe_plan(lambda: self.doc.GetFirstObject(),
                                               c4d.ID_BASEOBJECT_REL_SCALE, 'v', 0)
            self.assertEqual(steps, [('small', .001)])
            self.assertEqual(self.doc.GetFirstObject().GetRelScale(), c4d.Vector(1))
            self.assertTrue(any('probed 1.001' in line for line in ctrl.log))
            ctrl._cancel()

        def test_small_angle_probe_uses_display_degrees(self):
            self.phong()
            ctrl, steps = self.run_probe_plan(lambda: self.doc.GetFirstObject().GetTag(c4d.Tphong),
                                               c4d.PHONGTAG_PHONG_ANGLE, 'r')
            self.assertEqual(steps, [('small', .001)])
            tag = self.doc.GetFirstObject().GetTag(c4d.Tphong)
            self.assertAlmostEqual(tag[c4d.PHONGTAG_PHONG_ANGLE], plugin.math.radians(40))
            self.assertAlmostEqual(ctrl.param.scale, 180 / plugin.math.pi)
            ctrl._cancel()

        def test_small_probe_reverses_at_upper_limit(self):
            pid = (c4d.ID_USERDATA, self.ids[2][1].id)
            ctrl, steps = self.run_probe_plan(lambda: self.doc.GetFirstObject(), pid, 'r')
            self.assertEqual(steps, [('small', .001), ('small', -.001)])
            self.assertEqual(self.value(2), 1.0)
            ctrl._cancel()

        def test_integer_rounding_falls_back_to_whole_expression(self):
            pid = (c4d.ID_USERDATA, self.ids[1][1].id)
            ctrl, steps = self.run_probe_plan(lambda: self.doc.GetFirstObject(), pid, 'l')
            self.assertEqual(steps, [('small', .001), ('small', -.001), ('whole', 1)])
            self.assertEqual(self.value(1), 7)
            ctrl._cancel()

        def test_noop_undo_reacquires_handles_before_whole_fallback(self):
            pid = (c4d.ID_USERDATA, self.ids[1][1].id)
            ctrl, steps = self.run_probe_plan(lambda: self.doc.GetFirstObject(), pid, 'l', record_noops=True)
            self.assertEqual(steps[-1], ('whole', 1))
            self.assertEqual(self.value(1), 7)
            self.assertTrue(all(node.GetDocument() == self.doc for node in ctrl.targets))
            ctrl._cancel()

        def test_small_and_native_input_dispatch(self):
            with mock.patch.object(plugin, '_send_text') as text, \
                    mock.patch.object(plugin, '_send_key') as key:
                plugin._send_probe_step('small', .001)
                plugin._send_probe_step('small', -.001)
                self.assertEqual(text.call_args_list, [mock.call('+0.001'), mock.call('-0.001')])
                key.assert_not_called()
                plugin._send_probe_step('native', 1)
                key.assert_called_once_with(plugin.VK_UP, hold=.03)

        def test_unchanged_preserves_existing_undo_after_redo(self):
            self.native_change([(0, 14.5)])
            self.doc.DoUndo(False)
            self.doc.DoRedo()
            self.node = self.doc.GetFirstObject()
            ctrl = self.controller()
            ctrl._launch_focus_probe = mock.Mock()
            self.identify(ctrl)
            self.assertEqual(self.value(), 14.5)
            ctrl._launch_focus_probe.assert_called_once_with()
            self.assertFalse(ctrl.scrub_undo)

        def run_prepare(self, ctrl, click=None, commit=None):
            ctrl.focus_probe = ctrl.capturing = True
            ctrl.input_root = 123
            ctrl._input_ready = lambda: True
            ctrl._begin_ready = mock.Mock()
            ctrl._hide_cursor = mock.Mock()
            trace = []
            def mouse(flags):
                trace.append(('mouse', flags))
                if click:
                    click(flags)
            def key(vk):
                trace.append(('key', vk))
                if commit:
                    commit(vk)
            class ImmediateWorker:
                def __init__(self, target, **kwargs):
                    self.target = target
                def start(self):
                    self.target()
            with mock.patch.object(c4d.documents, 'GetActiveDocument', return_value=self.doc), \
                    mock.patch.object(plugin, '_send_key', side_effect=key), \
                    mock.patch.object(plugin, '_send_mouse', side_effect=mouse), \
                    mock.patch.object(plugin, '_move_to', side_effect=lambda *p: trace.append(('move', p))), \
                    mock.patch.object(plugin.user32, 'GetForegroundWindow', return_value=123), \
                    mock.patch.object(plugin.user32, 'GetAsyncKeyState', return_value=0), \
                    mock.patch.object(plugin.threading, 'Thread', ImmediateWorker), \
                    mock.patch.object(plugin.time, 'sleep'):
                ctrl._begin()
                ctrl.pump()
            return trace

        def test_pending_user_text_commits_before_baseline(self):
            ctrl = plugin.Controller(lambda: None)
            def commit(key):
                self.assertEqual(key, plugin.VK_RETURN)
                self.node[self.ids[0]] = 23.0
            self.run_prepare(ctrl, commit=commit)
            self.assertEqual(self.value(), 23.0)
            ctrl._begin_ready.assert_called_once_with()

        def test_automatic_selection_precedes_enter(self):
            ctrl = plugin.Controller(lambda: None)
            ctrl.origin, ctrl.pt = (140, 200), (150, 210)
            trace = self.run_prepare(ctrl)
            self.assertEqual(trace, [('move', (140, 200)),
                                    ('mouse', plugin.MOUSEEVENTF_LEFTDOWN),
                                    ('mouse', plugin.MOUSEEVENTF_LEFTUP),
                                    ('key', plugin.VK_RETURN), ('move', (150, 210))])
            ctrl._begin_ready.assert_called_once_with()
            self.assertFalse(ctrl.frozen)
            self.assertEqual(ctrl._hide_cursor.call_args_list, [mock.call(True), mock.call(False)])

        def test_release_during_selection_prevents_enter_and_probe(self):
            ctrl = plugin.Controller(lambda: None)
            def release(flags):
                if flags == plugin.MOUSEEVENTF_LEFTUP:
                    ctrl.released = True
            trace = self.run_prepare(ctrl, click=release)
            self.assertFalse(any(kind == 'key' for kind, _ in trace))
            ctrl._begin_ready.assert_not_called()
            self.assertEqual(ctrl.phase, plugin.IDLE)
            self.assertFalse(ctrl.capturing)

        def test_motion_during_selection_is_preserved(self):
            ctrl = plugin.Controller(lambda: None)
            ctrl.origin, ctrl.pt = (140, 200), (150, 210)
            def motion(flags):
                if flags == plugin.MOUSEEVENTF_LEFTUP:
                    ctrl.held_dx, ctrl.held_dy = 8, 5
            self.run_prepare(ctrl, click=motion)
            self.assertEqual(ctrl.pt, (158, 215))
            self.assertEqual(ctrl.out_dx, 8)

        def test_release_before_prepare_sends_no_input(self):
            ctrl = plugin.Controller(lambda: None)
            ctrl.focus_probe = ctrl.capturing = ctrl.released = True
            with mock.patch.object(plugin, '_send_key') as key, \
                    mock.patch.object(plugin, '_send_mouse') as mouse, \
                    mock.patch.object(plugin.threading, 'Thread') as thread:
                ctrl._begin()
            key.assert_not_called()
            mouse.assert_not_called()
            thread.assert_not_called()
            self.assertEqual(ctrl.phase, plugin.IDLE)

        def test_focus_loss_before_probe_sends_nothing(self):
            ctrl = self.controller()
            ctrl.capturing = True
            ctrl._input_ready = lambda: False
            with mock.patch.object(plugin, '_send_key') as send:
                ctrl._launch_focus_probe()
            send.assert_not_called()
            self.assertEqual(ctrl.phase, plugin.IDLE)
            self.assertFalse(ctrl.capturing)

        def test_physical_keyboard_cancels_automatic_probe(self):
            ctrl = self.controller()
            ctrl.capturing = True
            key = plugin.KBDLLHOOKSTRUCT(vkCode=plugin.VK_TAB, dwExtraInfo=0)
            with mock.patch.object(plugin.user32, 'CallNextHookEx', return_value=0):
                ctrl._proc_key(0, plugin.WM_KEYDOWN, plugin.ctypes.addressof(key))
            self.assertTrue(ctrl.cancelled)
            self.assertIn('cancel', ctrl.events)

        def test_injected_keyboard_does_not_cancel_probe(self):
            ctrl = self.controller()
            ctrl.capturing = True
            key = plugin.KBDLLHOOKSTRUCT(vkCode=plugin.VK_RETURN, dwExtraInfo=plugin.INJECT_MAGIC)
            with mock.patch.object(plugin.user32, 'CallNextHookEx', return_value=0):
                ctrl._proc_key(0, plugin.WM_KEYDOWN, plugin.ctypes.addressof(key))
            self.assertFalse(ctrl.cancelled)

        def test_direct_middle_button_starts_automatic_focus_probe(self):
            ctrl = self.controller()
            ctrl.phase = plugin.IDLE
            mouse = plugin.MSLLHOOKSTRUCT()
            mouse.pt.x, mouse.pt.y = 140, 200
            with mock.patch.object(ctrl, '_over_field', return_value=True), \
                    mock.patch.object(plugin.user32, 'WindowFromPoint', return_value=123), \
                    mock.patch.object(plugin.user32, 'GetAncestor', return_value=456):
                self.assertTrue(ctrl._on_mouse(plugin.WM_MBUTTONDOWN, mouse))
            self.assertTrue(ctrl.capturing)
            self.assertTrue(ctrl.focus_probe)
            self.assertEqual(ctrl.origin, (140, 200))
            self.assertEqual(ctrl.input_root, 456)
            self.assertIn('start', ctrl.events)
            self.assertFalse(plugin.ENABLE_LEGACY_PROBE)

        def test_middle_button_outside_field_passes_through(self):
            ctrl = self.controller()
            ctrl.phase = plugin.IDLE
            with mock.patch.object(ctrl, '_over_field', return_value=False):
                self.assertFalse(ctrl._on_mouse(plugin.WM_MBUTTONDOWN, plugin.MSLLHOOKSTRUCT()))
            self.assertFalse(ctrl.capturing)
            self.assertFalse(ctrl.events)

        def test_focus_loss_does_not_fall_back_to_legacy(self):
            ctrl = self.controller()
            ctrl.capturing = True
            ctrl._input_ready = lambda: False
            ctrl._begin_ready = mock.Mock()
            with mock.patch.object(c4d.documents, 'GetActiveDocument', return_value=self.doc):
                ctrl._begin()
            ctrl._begin_ready.assert_not_called()
            self.assertEqual(ctrl.phase, plugin.IDLE)

        def test_legacy_launch_is_blocked_without_input(self):
            ctrl = self.controller()
            ctrl.capturing = True
            with mock.patch.object(plugin, '_send_key') as key, \
                    mock.patch.object(plugin, '_send_mouse') as mouse, \
                    mock.patch.object(plugin.threading, 'Thread') as thread:
                ctrl._launch_scrub(1, (140, 200))
            key.assert_not_called()
            mouse.assert_not_called()
            thread.assert_not_called()
            self.assertEqual(ctrl.phase, plugin.IDLE)

        def test_label_descids_map_to_snapshot_keys(self):
            vec = lambda pid, comp: c4d.DescID(c4d.DescLevel(pid, c4d.DTYPE_VECTOR, 0),
                                               c4d.DescLevel(comp, c4d.DTYPE_REAL, 0))
            self.assertEqual(plugin._label_target(vec(c4d.ID_BASEOBJECT_REL_ROTATION, c4d.VECTOR_Y)),
                             (c4d.ID_BASEOBJECT_REL_ROTATION, 'v', 1))
            self.assertEqual(plugin._label_target(c4d.DescID(c4d.DescLevel(c4d.PRIM_CUBE_SUBX, c4d.DTYPE_LONG, 0))),
                             (c4d.PRIM_CUBE_SUBX, 'l', None))
            # A shared vector label: the pressed field picks the component.
            self.assertEqual(plugin._label_target(
                c4d.DescID(c4d.DescLevel(c4d.ID_BASEOBJECT_REL_SCALE, c4d.DTYPE_VECTOR, 0))),
                (c4d.ID_BASEOBJECT_REL_SCALE, 'v', None))
            self.assertEqual(plugin._label_target(self.ids[0]), ((c4d.ID_USERDATA, 1), 'r', None))
            self.assertIsNone(plugin._label_target(
                c4d.DescID(c4d.DescLevel(c4d.ID_BASELIST_NAME, c4d.DTYPE_STRING, 0))))

        def label_ladder(self, descid, nodes, travel=3):
            ctrl = self.controller()
            ctrl.phase = plugin.PREPARING
            ctrl._open_overlay = lambda: setattr(ctrl, 'overlay_rect', (0, 0, 52, 416))
            ctrl.out_dx = plugin.PX_PER_STEP * travel
            ctrl.pt = (100, 0)   # beside the ladder: the 1.0 rung stays selected
            with mock.patch.object(plugin, '_cursors_scrub'), \
                    mock.patch.object(plugin, '_active_rungs', return_value=list(plugin.RUNGS)):
                ok = ctrl._begin_label((descid, nodes))
            return ctrl, ok

        def test_label_document_setting_scrubs_directly(self):
            fps = self.doc.GetFps()
            descid = c4d.DescID(c4d.DescLevel(c4d.DOCUMENT_FPS, c4d.DTYPE_LONG, 0))
            ctrl, ok = self.label_ladder(descid, [self.doc])
            self.assertTrue(ok)
            self.assertEqual(ctrl.phase, plugin.LADDER)
            self.assertEqual(ctrl.targets, [self.doc])
            self.assertFalse(ctrl.scrub_undo)          # no AddUndo for settings
            self.assertEqual(self.doc.GetFps(), fps + 3)
            with mock.patch.object(plugin, '_cursors_scrub'):
                ctrl._cancel()
            self.assertEqual(self.doc.GetFps(), fps)

        def test_label_project_scale_uses_unit_adapter(self):
            descid = c4d.DescID(c4d.DescLevel(c4d.DOCUMENT_DOCUNIT, c4d.CUSTOMDATATYPE_UNITSCALE, 0))
            scale = self.doc[c4d.DOCUMENT_DOCUNIT].GetUnitScale()[0]
            ctrl, ok = self.label_ladder(descid, [self.doc])
            self.assertTrue(ok)
            self.assertIsInstance(ctrl.targets[0], plugin._UnitScale)
            self.assertAlmostEqual(self.doc[c4d.DOCUMENT_DOCUNIT].GetUnitScale()[0], scale + 3)
            with mock.patch.object(plugin, '_cursors_scrub'):
                ctrl._cancel()
            self.assertAlmostEqual(self.doc[c4d.DOCUMENT_DOCUNIT].GetUnitScale()[0], scale)

        def test_unresolved_label_without_fallback_sends_no_input(self):
            ctrl = self.controller()
            ctrl.focus_probe = ctrl.capturing = True
            ctrl.label_tried = True
            ctrl._input_ready = lambda: True
            with mock.patch.object(plugin, 'ENABLE_PROBE_FALLBACK', False), \
                    mock.patch.object(plugin, '_send_mouse') as mouse, \
                    mock.patch.object(plugin, '_send_key') as key, \
                    mock.patch.object(c4d.documents, 'GetActiveDocument', return_value=self.doc):
                ctrl._begin()
            mouse.assert_not_called()
            key.assert_not_called()
            self.assertEqual(ctrl.phase, plugin.IDLE)

        def test_label_ladder_adds_one_undo(self):
            ctrl = self.controller()
            ctrl.phase = plugin.PREPARING
            ctrl._open_overlay = lambda: setattr(ctrl, 'overlay_rect', (0, 0, 52, 416))
            descid = c4d.DescID(c4d.DescLevel(c4d.ID_BASEOBJECT_REL_POSITION, c4d.DTYPE_VECTOR, 0),
                                c4d.DescLevel(c4d.VECTOR_X, c4d.DTYPE_REAL, 0))
            ctrl.out_dx = plugin.PX_PER_STEP * 3
            ctrl.pt = (100, 0)   # beside the ladder: the 1.0 rung stays selected
            with mock.patch.object(plugin, '_cursors_scrub'):
                self.assertTrue(ctrl._begin_label((descid, [self.node])))
                self.assertEqual(ctrl.phase, plugin.LADDER)
                self.assertTrue(ctrl.scrub_undo)
                self.assertEqual(self.node.GetRelPos().x, 3)
                ctrl._finish()
            self.doc.DoUndo(False)
            self.assertEqual(self.doc.GetFirstObject().GetRelPos().x, 0)

        def test_node_port_label_is_decoded(self):
            port = 'com.redshift3d.redshift4c4d.nodes.core.openpbrmaterial.base_weight'
            raw = port.encode('ascii')
            raw += b'\0' * (-len(raw) % 4)
            levels = [c4d.DescLevel(int.from_bytes(raw[i:i + 4], 'big', signed=True), c4d.DTYPE_SUBCONTAINER, 0)
                      for i in range(0, len(raw), 4)]
            descid = c4d.DescID()
            for level in levels + [c4d.DescLevel(1, c4d.DTYPE_REAL, 0)]:
                descid.PushId(level)
            target = plugin._label_target(descid)
            self.assertEqual(target, ('port', port, None))
            key = 'material:graph:openpbrmaterial@abc<' + port
            self.assertTrue(plugin._label_matches(target, (key, 'r', None)))
            self.assertFalse(plugin._label_matches(target, (key + '_x', 'r', None)))
            self.assertFalse(plugin._label_matches(target, (c4d.PRIM_CUBE_SUBX, 'l', None)))

        def test_short_scene_node_port_id_is_decoded(self):
            raw = b'pointin\0'
            descid = c4d.DescID()
            for i in (0, 4):
                descid.PushId(c4d.DescLevel(int.from_bytes(raw[i:i + 4], 'big', signed=True),
                                            c4d.DTYPE_SUBCONTAINER, 0))
            descid.PushId(c4d.DescLevel(1, c4d.DTYPE_VECTOR, 0))
            target = plugin._label_target(descid)
            self.assertEqual(target, ('port', 'pointin', None))
            self.assertTrue(plugin._label_matches(target, ('scene:x:addpoint@Hz<pointin', 'v', None)))
            self.assertFalse(plugin._label_matches(target, ('scene:x:addpoint@Hz<xpointin', 'v', None)))

        def test_shared_vector_label_names_pressed_field(self):
            self.assertEqual([plugin._label_comp('v', None, f) for f in range(4)], [0, 1, 2, False])
            self.assertEqual(plugin._label_comp('v', 1, 0), 1)       # P . Y
            self.assertIs(plugin._label_comp('v', 1, 1), False)
            self.assertIsNone(plugin._label_comp('r', None, 0))
            self.assertIs(plugin._label_comp('r', None, 1), False)  # second field of a pair

        def row_buffer(self, det):
            w, h = det.WIDTH, det.HEIGHT
            buf = (plugin.ctypes.c_ubyte * (w * h * 4))()

            def put(x, y, rgb):
                i = (y * w + x) * 4
                buf[i], buf[i + 1], buf[i + 2] = rgb[2], rgb[1], rgb[0]
            for y in range(h):
                for x in range(w):
                    put(x, y, (43, 43, 43))
            return buf, put

        def test_vector_row_fields_share_label(self):
            det = plugin.FieldDetector()
            det.edit_colors = [(28, 28, 28)]
            buf, put = self.row_buffer(det)
            top, bottom = 28, 43
            for y in range(31, 41):                   # label text "Point"
                for x in range(150, 185):
                    if (x + y) % 3:
                        put(x, y, (200, 200, 200))
            lefts = (230, 312, 394)                   # X, Y, Z fields, 3 px apart
            for left in lefts:
                for y in range(top, bottom + 1):
                    for x in range(left, left + 79):
                        put(x, y, (28, 28, 28))
                for y in range(33, 39):               # "0 cm"
                    for x in range(left + 30, left + 50):
                        if x % 2:
                            put(x, y, (220, 220, 220))
            for x in range(lefts[1] - 1, lefts[1] + 80):  # Y focused: blue border
                put(x, top - 1, (90, 120, 230))
                put(x, bottom + 1, (90, 120, 230))
            for y in range(top - 1, bottom + 2):
                put(lefts[1] - 1, y, (90, 120, 230))
                put(lefts[1] + 79, y, (90, 120, 230))
            for i, left in enumerate(lefts):
                point = det._label(buf, left, top, bottom, 1.0)
                self.assertIsNotNone(point, i)
                self.assertTrue(150 <= point[0] < 185, point)
                self.assertEqual(point[2], i)

        def test_fields_scale_with_dpi(self):
            det = plugin.FieldDetector()
            det.edit_colors = [(28, 28, 28)]
            w, h = det.WIDTH * 2, det.HEIGHT * 2
            buf = (plugin.ctypes.c_ubyte * (w * h * 4))()
            for i in range(w * h):
                buf[i * 4:i * 4 + 3] = [43, 43, 43]
            for y in range(h // 2 - 26, h // 2 + 27):     # a 53 px tall field at 200 %
                for x in range(w // 2 - 60, w // 2 + 100):
                    buf[(y * w + x) * 4:(y * w + x) * 4 + 3] = [28, 28, 28]
            with mock.patch.object(plugin, '_grab_screen', return_value=buf):
                self.assertEqual(det.check(1000, 500, 2.0)[0], 'field')
                self.assertNotEqual(det.check(1000, 500, 1.0)[0], 'field')
            ctrl = self.controller()
            ctrl.dpi_scale = 2.0
            self.assertEqual(ctrl._rung_px(), plugin.RUNG_PX * 2)

        def test_node_editor_grid_is_not_a_field(self):
            det = plugin.FieldDetector()
            det.edit_colors = [(28, 28, 28)]
            buf, put = self.row_buffer(det)
            for y in range(det.HEIGHT):
                for x in range(det.WIDTH):
                    line = (x + 16) % 32 == 0 or (y + 16) % 32 == 0
                    put(x, y, (36, 36, 36) if line else (28, 28, 28))
            with mock.patch.object(plugin, '_grab_screen', return_value=buf):
                self.assertEqual(det.check(1000 + 16, 500 + 4)[0], 'grid')

        def test_label_skips_port_circle(self):
            det = plugin.FieldDetector()
            det.edit_colors = [(28, 28, 28)]
            w, h = det.WIDTH, det.HEIGHT
            buf = (plugin.ctypes.c_ubyte * (w * h * 4))()
            def put(x, y, rgb):
                i = (y * w + x) * 4
                buf[i], buf[i + 1], buf[i + 2] = rgb[2], rgb[1], rgb[0]
            for y in range(h):
                for x in range(w):
                    put(x, y, (43, 43, 43))
            top, bottom, field_left = 26, 45, 300
            for y in range(top, bottom + 1):          # the field
                for x in range(field_left, field_left + 80):
                    put(x, y, (28, 28, 28))
            for y in range(30, 41):                   # label text "Weight"
                for x in range(200, 240):
                    if (x + y) % 3:
                        put(x, y, (200, 200, 200))
            ring = [(0, 2), (0, 3), (0, 4), (0, 5), (7, 2), (7, 3), (7, 4), (7, 5),
                    (2, 0), (3, 0), (4, 0), (5, 0), (2, 7), (3, 7), (4, 7), (5, 7),
                    (1, 1), (6, 1), (1, 6), (6, 6)]
            for dx, dy in ring:                       # port circle 8 x 8
                put(278 + dx, 32 + dy, (200, 200, 200))
            point = det._label(buf, field_left, top, bottom, 1.0)
            self.assertIsNotNone(point)
            self.assertTrue(200 <= point[0] < 240, point)
            for dx in range(8):                       # a connected port: filled
                for dy in range(1, 7):
                    put(278 + dx, 32 + dy, (200, 200, 200))
            point = det._label(buf, field_left, top, bottom, 1.0)
            self.assertTrue(point is None or 200 <= point[0] < 240, point)

        def test_startup_preference_leaves_session_state(self):
            bc = c4d.BaseContainer()
            ctrl = plugin.Controller(lambda: None)
            descid = c4d.DescID(c4d.DescLevel(plugin.PREFS_ENABLE_AT_STARTUP, c4d.DTYPE_BOOL, 0))
            prefs = plugin.ValueLadderPrefs()
            with mock.patch.object(plugin, '_prefs', return_value=bc), \
                    mock.patch.object(plugin, '_controller', ctrl):
                for session in (True, False):
                    ctrl.enabled = session
                    for startup in (False, True):
                        prefs.SetDParameter(None, descid, startup, c4d.DESCFLAGS_SET_NONE)
                        self.assertEqual(plugin._enable_at_startup(), startup)
                        self.assertEqual(ctrl.enabled, session)
                ctrl.enabled = True
                plugin.ValueLadderToggle().Execute(self.doc)
                self.assertFalse(ctrl.enabled)
                self.assertTrue(plugin._enable_at_startup())

        def test_preferences_migrate_from_test_id(self):
            world = c4d.BaseContainer()
            legacy = c4d.BaseContainer()
            legacy.SetBool(plugin.PREFS_ENABLE_AT_STARTUP, False)
            legacy.SetBool(plugin.PREFS_RUNG_BASE, True)          # 10000 on
            legacy.SetString(4242, 'unrelated')
            world.SetContainer(plugin.LEGACY_PREFS_KEY, legacy)
            with mock.patch.object(c4d, 'GetWorldContainerInstance', return_value=world):
                self.assertFalse(plugin._enable_at_startup())
                self.assertEqual(plugin._active_rungs()[0], 10000.0)
                migrated = world.GetContainer(plugin.PLUGIN_ID)
                self.assertEqual(migrated.GetString(4242), '')      # only ValueLadder's keys
                # Once migrated, later changes to the old key are ignored.
                legacy.SetBool(plugin.PREFS_ENABLE_AT_STARTUP, True)
                world.SetContainer(plugin.LEGACY_PREFS_KEY, legacy)
                self.assertFalse(plugin._enable_at_startup())
            fresh = c4d.BaseContainer()
            with mock.patch.object(c4d, 'GetWorldContainerInstance', return_value=fresh):
                self.assertTrue(plugin._enable_at_startup())      # defaults without old settings
                self.assertEqual(plugin._active_rungs(), plugin.RUNGS)

        def test_session_switch_on_preferences_page(self):
            bc = c4d.BaseContainer()
            ctrl = plugin.Controller(lambda: None)
            ctrl._receiver_ready = mock.Mock(return_value=True)
            now = c4d.DescID(c4d.DescLevel(plugin.PREFS_ENABLED_NOW, c4d.DTYPE_BOOL, 0))
            prefs = plugin.ValueLadderPrefs()
            with mock.patch.object(plugin, '_prefs', return_value=bc), \
                    mock.patch.object(plugin, '_controller', ctrl):
                bc.SetBool(plugin.PREFS_ENABLE_AT_STARTUP, False)
                ctrl.enabled = False
                prefs.SetDParameter(None, now, True, c4d.DESCFLAGS_SET_NONE)
                self.assertTrue(ctrl.enabled)
                ctrl._receiver_ready.assert_called_once()
                self.assertTrue(prefs.GetDParameter(None, now, c4d.DESCFLAGS_GET_NONE)[1])
                self.assertFalse(plugin._enable_at_startup())    # startup untouched
                self.assertFalse(bc.GetBool(plugin.PREFS_ENABLED_NOW))  # never stored
                plugin.ValueLadderToggle().Execute(self.doc)     # the menu flips the same state
                self.assertFalse(prefs.GetDParameter(None, now, c4d.DESCFLAGS_GET_NONE)[1])

        def test_rung_helpers(self):
            self.assertEqual(plugin._home_rung(plugin.RUNGS), plugin.RUNGS.index(1.0))
            self.assertEqual(plugin._home_rung([100.0, 10.0, 0.1]), 1)
            self.assertEqual(plugin._int_last_rung([10000.0, 10.0, 1.0, 0.01]), 2)
            self.assertEqual(plugin._int_last_rung([0.1, 0.01]), 0)
            self.assertEqual([plugin._rung_pref(plugin.PREFS_RUNG_BASE + i)
                              for i in range(len(plugin.ALL_RUNGS))], plugin.ALL_RUNGS)
            self.assertIsNone(plugin._rung_pref(plugin.PREFS_ENABLE_AT_STARTUP))

        def test_chosen_rungs_drive_ladder_and_integers(self):
            bc = c4d.BaseContainer()
            for rung in plugin.ALL_RUNGS:
                bc.SetBool(plugin.PREFS_RUNG_BASE + plugin.ALL_RUNGS.index(rung), rung in (100.0, 0.5, 0.1))
            with mock.patch.object(plugin, '_prefs', return_value=bc):
                self.assertEqual(plugin._active_rungs(), [100.0, 0.1])
                ctrl = self.controller()
                ctrl._reset_rungs()
            self.assertEqual((ctrl.rungs, ctrl.rung, ctrl.last_rung), ([100.0, 0.1], 1, 1))
            # Integer parameter on the 0.1 rung: falls back to the 100 rung.
            self.native_change([(1, 8)])
            ctrl.focus_attempt = 4   # the whole +1 probe
            self.identify(ctrl)
            self.assertEqual(ctrl.phase, plugin.LADDER)
            self.assertEqual((ctrl.rungs, ctrl.rung, ctrl.last_rung), ([100.0], 0, 0))
            ctrl._cancel()

        def test_integer_ladder_shrinks_open_overlay(self):
            ctrl = self.controller()
            ctrl.overlay_rect = (10, 20, plugin.OVERLAY_W, plugin.RUNG_PX * (len(ctrl.rungs) + 1))
            ctrl.overlay = mock.Mock()
            self.native_change([(1, 8)])
            ctrl.out_dx = plugin.PX_PER_STEP
            self.identify(ctrl)
            height = plugin.RUNG_PX * (plugin.INT_LAST_RUNG + 2)
            self.assertEqual(ctrl.overlay_rect, (10, 20, plugin.OVERLAY_W, height))
            self.assertEqual(ctrl.ladder_rect, (10, 20, 10 + plugin.OVERLAY_W, 20 + height))
            self.assertEqual(ctrl.overlay.rect, ctrl.overlay_rect)
            ctrl.overlay.refresh.assert_called()
            ctrl._cancel()

        def test_all_rungs_off_keeps_defaults(self):
            bc = c4d.BaseContainer()
            for i in range(len(plugin.ALL_RUNGS)):
                bc.SetBool(plugin.PREFS_RUNG_BASE + i, False)
            with mock.patch.object(plugin, '_prefs', return_value=bc):
                self.assertEqual(plugin._active_rungs(), plugin.RUNGS)

        def test_disappearing_adapter_is_not_a_target(self):
            class Gone:
                def vl_snapshot(self):
                    raise RuntimeError('graph was closed')
            self.assertEqual(plugin._snapshot(Gone()), {})

        def test_failed_restore_rolls_back(self):
            ctrl = self.controller()
            self.native_change([(0, 12.501)])
            with mock.patch.object(plugin.Param, 'set', return_value=None):
                self.identify(ctrl)
            self.assertEqual(ctrl.phase, plugin.IDLE)
            self.assertEqual(self.value(), 12.5)

        def test_visible_psr_components_are_unambiguous(self):
            for pid in (c4d.ID_BASEOBJECT_REL_POSITION, c4d.ID_BASEOBJECT_REL_ROTATION,
                        c4d.ID_BASEOBJECT_REL_SCALE):
                for comp in range(3):
                    with self.subTest(pid=pid, component=comp):
                        self.doc.FlushUndoBuffer()
                        self.node = self.doc.GetFirstObject()
                        descid = plugin._descid(pid, 'v')
                        original = self.node.GetParameter(descid, c4d.DESCFLAGS_GET_NONE)
                        ctrl = self.controller()
                        changed = c4d.Vector(original)
                        changed[comp] += .01
                        self.doc.StartUndo()
                        self.doc.AddUndo(c4d.UNDOTYPE_CHANGE, self.node)
                        self.node.SetParameter(descid, changed, c4d.DESCFLAGS_SET_NONE)
                        self.doc.EndUndo()
                        self.assertEqual(list(plugin._diff(ctrl.before[0], plugin._snapshot(self.node))),
                                         [(pid, 'v', comp)])
                        ctrl.out_dx = plugin.PX_PER_STEP * 2
                        self.identify(ctrl)
                        self.assertEqual(ctrl.phase, plugin.LADDER)
                        self.assertEqual((ctrl.param.pid, ctrl.param.comp), (pid, comp))
                        result = self.node.GetParameter(descid, c4d.DESCFLAGS_GET_NONE)
                        expected = c4d.Vector(original)
                        expected[comp] += 2 / ctrl.param.scale
                        for index in range(3):
                            self.assertAlmostEqual(result[index], expected[index])
                        ctrl._cancel()
                        restored = self.doc.GetFirstObject().GetParameter(descid, c4d.DESCFLAGS_GET_NONE)
                        self.assertEqual(restored, original)

        def test_hidden_derived_transform_values_are_excluded(self):
            snapshot = plugin._snapshot(self.node)
            for pid in (c4d.ID_BASEOBJECT_GLOBAL_POSITION, c4d.ID_BASEOBJECT_GLOBAL_ROTATION,
                        c4d.ID_BASEOBJECT_ABS_POSITION, c4d.ID_BASEOBJECT_ABS_ROTATION,
                        c4d.ID_BASEOBJECT_ABS_SCALE):
                self.assertNotIn(pid, snapshot)

        def test_parent_transform_does_not_create_extra_targets(self):
            parent = c4d.BaseObject(c4d.Onull)
            self.doc.InsertObject(parent)
            self.node.InsertUnder(parent)
            parent.SetRelRot(c4d.Vector(.7, .3, .2))
            parent.SetRelScale(c4d.Vector(2, 3, 4))
            ctrl = self.controller()
            self.node.SetRelPos(c4d.Vector(.01, 0, 0))
            ctrl.out_dx = plugin.PX_PER_STEP
            self.identify(ctrl)
            self.assertEqual(ctrl.phase, plugin.LADDER)
            self.assertEqual(ctrl.param.pid, c4d.ID_BASEOBJECT_REL_POSITION)
            self.assertEqual(self.node.GetRelPos(), c4d.Vector(1, 0, 0))
            ctrl._cancel()
            self.assertEqual(self.node.GetRelPos(), c4d.Vector())

        def phong(self):
            tag = c4d.BaseTag(c4d.Tphong)
            self.node.InsertTag(tag)
            tag[c4d.PHONGTAG_PHONG_ANGLELIMIT] = True
            tag[c4d.PHONGTAG_PHONG_ANGLE] = plugin.math.radians(40)
            self.doc.SetActiveObject(self.node, c4d.SELECTION_NEW)
            return tag

        def test_object_integrated_phong_tag_is_a_candidate(self):
            tag = self.phong()
            self.assertEqual(self.doc.GetActiveTags(), [])
            candidates = plugin._candidates(self.doc)
            self.assertEqual(sum(node == tag for node in candidates), 1)
            self.assertIn(c4d.PHONGTAG_PHONG_ANGLE, plugin._snapshot(tag))

        def test_selected_attached_tag_is_not_duplicated(self):
            tag = self.phong()
            self.doc.SetActiveTag(tag, c4d.SELECTION_NEW)
            candidates = plugin._candidates(self.doc)
            self.assertEqual(sum(node == tag for node in candidates), 1)

        def test_attached_tag_scan_is_bounded(self):
            self.phong()
            self.node.InsertTag(c4d.BaseTag(c4d.Tphong))
            with mock.patch.object(plugin, 'MAX_ATTACHED_TAGS', 1):
                self.assertEqual(len(plugin._attached_tags([self.node])), 1)

        def test_integrated_phong_angle_probe_units_undo_and_redo(self):
            tag = self.phong()
            ctrl = self.controller()
            ctrl.cands = plugin._candidates(self.doc)
            ctrl.before = [plugin._snapshot(node) for node in ctrl.cands]
            # Match the live worker's pre-input noise check: the first SDK
            # description read can initialize unrelated document defaults.
            ctrl.noisy = [{(key, comp) for key, _, comp in plugin._diff(before, plugin._snapshot(node))}
                          for node, before in zip(ctrl.cands, ctrl.before)]
            ctrl.undo_marks = [(tag, False)]
            self.doc.StartUndo()
            self.doc.AddUndo(c4d.UNDOTYPE_CHANGE, tag)
            tag[c4d.PHONGTAG_PHONG_ANGLE] = plugin.math.radians(40.1)
            self.doc.EndUndo()
            ctrl.out_dx = plugin.PX_PER_STEP * 2
            self.identify(ctrl)
            self.assertEqual(ctrl.phase, plugin.LADDER)
            self.assertEqual(ctrl.targets, [tag])
            self.assertEqual(ctrl.param.pid, c4d.PHONGTAG_PHONG_ANGLE)
            self.assertAlmostEqual(tag[c4d.PHONGTAG_PHONG_ANGLE], plugin.math.radians(42))
            ctrl._finish()
            self.doc.DoUndo(False)
            restored = self.doc.GetFirstObject().GetTag(c4d.Tphong)
            self.assertAlmostEqual(restored[c4d.PHONGTAG_PHONG_ANGLE], plugin.math.radians(40))
            self.doc.DoRedo()
            restored = self.doc.GetFirstObject().GetTag(c4d.Tphong)
            self.assertAlmostEqual(restored[c4d.PHONGTAG_PHONG_ANGLE], plugin.math.radians(42))

    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(FocusChecks))
    if not result.wasSuccessful():
        raise AssertionError('Focused probe checks failed')
    return result


if __name__ == '__main__':
    main()
