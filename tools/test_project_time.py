"""Project FPS/time SDK checks on disposable, inactive documents."""
import unittest
from unittest import mock

import c4d

from test_focus_probe import _load_plugin


def main():
    plugin = _load_plugin()
    fields = (c4d.DOCUMENT_TIME, c4d.DOCUMENT_MINTIME, c4d.DOCUMENT_MAXTIME,
              c4d.DOCUMENT_LOOPMINTIME, c4d.DOCUMENT_LOOPMAXTIME)

    class ProjectTimeChecks(unittest.TestCase):
        def setUp(self):
            self.doc = c4d.documents.BaseDocument()
            self.addCleanup(c4d.documents.KillDocument, self.doc)
            self.description = self.doc.GetDescription(c4d.DESCFLAGS_DESC_NONE)
            self.ids = {did[0].id: did for _, did, _ in self.description if did.GetDepth() == 1}
            # Initialize the new document's public setting defaults before
            # recording baselines, just as opening Project Settings does.
            self.doc.SetParameter(self.ids[c4d.DOCUMENT_FPS], 30, c4d.DESCFLAGS_SET_USERINTERACTION)
            self.doc.FlushUndoBuffer()

        def state(self):
            return self.doc.GetFps(), tuple(self.doc[pid].Get() for pid in fields)

        def controller(self):
            ctrl = plugin.Controller(lambda: None)
            ctrl.doc = self.doc
            ctrl.cands = [self.doc]
            ctrl.before = [plugin._snapshot(self.doc)]
            ctrl.noisy = [set()]
            ctrl.undo_before = self.doc.GetUndoPtr()
            ctrl.undo_marks = [(self.doc, False)]
            ctrl.phase = plugin.IDENTIFYING
            ctrl.focus_probe = True
            ctrl.focus_attempt = 1
            ctrl.resume_pt = (0, 0)
            ctrl._redraw = lambda **kwargs: None
            return ctrl

        def native_change(self, values):
            self.doc.StartUndo()
            self.doc.AddUndo(c4d.UNDOTYPE_CHANGE, self.doc)
            for pid, value in values:
                self.doc.SetParameter(self.ids[pid], value, c4d.DESCFLAGS_SET_USERINTERACTION)
            self.doc.EndUndo()

        def identify(self, ctrl):
            with mock.patch.object(plugin, '_cursor_pos', return_value=(0, 0)), \
                    mock.patch.object(c4d.documents, 'GetActiveDocument', return_value=self.doc):
                ctrl._finish_identify()

        def test_fps_snapshot_has_no_derived_time_changes(self):
            before = plugin._snapshot(self.doc)
            self.doc.SetFps(31)
            changed = list(plugin._diff(before, plugin._snapshot(self.doc)))
            self.assertEqual(changed, [(c4d.DOCUMENT_FPS, 'l', None)])
            self.assertEqual(before[c4d.DOCUMENT_MAXTIME], ('t', c4d.BaseTime(90, 30)))

        def test_fps_identification_restoration_and_cancel(self):
            original = self.state()
            ctrl = self.controller()
            self.native_change([(c4d.DOCUMENT_FPS, 31)])
            self.identify(ctrl)
            self.assertEqual(ctrl.phase, plugin.LADDER, list(ctrl.log))
            self.assertEqual(ctrl.param.pid, c4d.DOCUMENT_FPS)
            self.assertEqual(self.state(), original)
            ctrl.param.set(self.doc, 32)
            self.assertEqual(self.doc.GetFps(), 32)
            ctrl._cancel()
            self.assertEqual(self.state(), original)

        def test_all_five_time_fields_identify_and_restore(self):
            for pid, frame in zip(fields, (1, 1, 91, 1, 89)):
                with self.subTest(pid=pid):
                    self.doc.FlushUndoBuffer()
                    original = self.state()
                    ctrl = self.controller()
                    self.native_change([(pid, c4d.BaseTime(frame, 30))])
                    self.identify(ctrl)
                    self.assertEqual(ctrl.phase, plugin.LADDER, list(ctrl.log))
                    self.assertEqual(ctrl.param.pid, pid)
                    self.assertEqual(self.state(), original)
                    ctrl.param.set(self.doc, frame)
                    self.assertAlmostEqual(ctrl.param.get(self.doc), frame)
                    ctrl._cancel()
                    self.assertEqual(self.state(), original)

        def test_minimum_probe_restores_clamped_preview_minimum(self):
            original = plugin._snapshot(self.doc)
            self.doc.SetMinTime(c4d.BaseTime(1, 30))
            after = plugin._snapshot(self.doc)
            self.assertEqual({key for key, _, _ in plugin._diff(original, after)},
                             {c4d.DOCUMENT_MINTIME, c4d.DOCUMENT_LOOPMINTIME})
            self.assertEqual(list(plugin._probe_diff(self.doc, original, after)),
                             [(c4d.DOCUMENT_MINTIME, 't', None)])
            param = plugin.Param(self.doc, c4d.DOCUMENT_MINTIME, 't', None, snapshot=original)
            param.restore(self.doc, 0)
            self.assertEqual(plugin._snapshot(self.doc), original)

        def test_maximum_probe_restores_custom_preview_range(self):
            self.doc.SetLoopMinTime(c4d.BaseTime(5, 30))
            self.doc.SetLoopMaxTime(c4d.BaseTime(89, 30))
            original = self.state()
            ctrl = self.controller()
            self.native_change([(c4d.DOCUMENT_MAXTIME, c4d.BaseTime(88, 30))])
            self.identify(ctrl)
            self.assertEqual(ctrl.phase, plugin.LADDER, list(ctrl.log))
            self.assertEqual(ctrl.param.pid, c4d.DOCUMENT_MAXTIME)
            self.assertEqual(self.state(), original)
            ctrl.param.set(self.doc, 87)
            ctrl._cancel()
            self.assertEqual(self.state(), original)

        def test_independent_time_and_fps_changes_remain_ambiguous(self):
            original = self.state()
            ctrl = self.controller()
            self.native_change([(c4d.DOCUMENT_FPS, 31),
                                (c4d.DOCUMENT_TIME, c4d.BaseTime(1, 31))])
            self.identify(ctrl)
            self.assertEqual(ctrl.phase, plugin.IDLE)
            self.assertEqual(self.state(), original)

        def test_time_snapshots_and_restore_preserve_subframes(self):
            self.doc.SetTime(c4d.BaseTime(1, 60))
            before = plugin._snapshot(self.doc)
            self.assertAlmostEqual(before[c4d.DOCUMENT_TIME][1].Get(), .5 / 30)
            self.doc.SetTime(c4d.BaseTime(501, 30000))
            self.assertIn((c4d.DOCUMENT_TIME, 't', None),
                          list(plugin._diff(before, plugin._snapshot(self.doc))))
            param = plugin.Param(self.doc, c4d.DOCUMENT_TIME, 't', None, snapshot=before)
            param.restore(self.doc, param.from_snapshot(before[c4d.DOCUMENT_TIME][1]))
            self.assertAlmostEqual(self.doc.GetTime().Get() * 30, .5)

        def test_whole_probe_expression_dispatch(self):
            with mock.patch.object(plugin, '_send_text') as text:
                plugin._send_probe_step('whole', 1)
                plugin._send_probe_step('whole', -1)
            self.assertEqual(text.call_args_list, [mock.call('+1'), mock.call('-1')])
            self.assertEqual(plugin.FOCUS_PROBES[:4],
                             (('small', .001), ('small', -.001), ('whole', 1), ('whole', -1)))

        def test_identification_and_zero_travel_preserve_subframes(self):
            self.doc.SetTime(c4d.BaseTime(1, 60))
            original = self.state()
            ctrl = self.controller()
            self.native_change([(c4d.DOCUMENT_TIME, c4d.BaseTime(3, 60))])
            self.identify(ctrl)
            self.assertEqual(ctrl.phase, plugin.LADDER, list(ctrl.log))
            self.assertEqual(self.state(), original)
            ctrl.param.set(self.doc, 2)
            ctrl.steps = None
            ctrl._update((0, 0), 0)
            self.assertEqual(self.state(), original)
            ctrl._cancel()
            self.assertEqual(self.state(), original)

        def test_unexpected_preview_change_is_not_hidden(self):
            before = plugin._snapshot(self.doc)
            self.doc.SetMinTime(c4d.BaseTime(1, 30))
            self.doc.SetLoopMaxTime(c4d.BaseTime(80, 30))
            changed = list(plugin._probe_diff(self.doc, before, plugin._snapshot(self.doc)))
            self.assertGreater(len(changed), 1)

    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(ProjectTimeChecks))
    if not result.wasSuccessful():
        raise AssertionError('Project time SDK checks failed')
    return result


if __name__ == '__main__':
    main()
