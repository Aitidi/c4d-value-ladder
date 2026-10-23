"""C4D SDK checks against a disposable copy of an existing node material.

run_checks(source) never writes to source or switches the active document.
The source graph must contain a node with an editable real input.
"""
import io
import unittest
from unittest import mock

import c4d
import maxon

from test_focus_probe import _load_plugin


def run_checks(source):
    plugin = _load_plugin()
    space = c4d.GetActiveNodeSpaceId()

    class MaterialChecks(unittest.TestCase):
        def setUp(self):
            self.doc = c4d.documents.BaseDocument()
            self.addCleanup(c4d.documents.KillDocument, self.doc)
            self.mat = source.GetClone()
            self.doc.InsertMaterial(self.mat)
            self.mat.DelBit(c4d.BIT_ACTIVE)
            self.graph = self.mat.GetNodeMaterialReference().GetGraph(space)
            self.nodes = self.graph.GetViewRoot().GetChildren(mask=maxon.NODE_KIND.NODE)
            self.adapter = self.key = None
            for node in self.nodes:
                adapter = plugin._NodePorts(self.mat, self.graph, node)
                for key, (kind, value) in adapter.vl_snapshot().items():
                    if kind == 'r':
                        self.adapter, self.key = adapter, key
                        break
                if self.adapter is not None:
                    break
            self.assertIsNotNone(self.adapter, 'Fixture needs an editable real input')
            options = maxon.DataDictionary()
            options.Set(maxon.nodes.UndoMode, maxon.nodes.UNDO_MODE.NONE)
            with self.graph.BeginTransaction(options) as transaction:
                maxon.GraphModelHelper.DeselectAll(self.graph, maxon.NODE_KIND.NODE)
                maxon.GraphModelHelper.SelectNode(self.graph.GetNode(self.adapter.path))
                transaction.Commit()
            self.original = self.adapter.GetParameter((self.key, None), 0)
            self.doc.FlushUndoBuffer()

        def test_unselected_material_with_selected_node_is_discovered(self):
            self.assertEqual(self.doc.GetActiveMaterials(), [])
            found = plugin._node_ports([], self.doc)
            self.assertTrue(any(n.material == self.mat and n.path == self.adapter.path for n in found))
            self.assertTrue(any(isinstance(n, plugin._NodePorts)
                                for n in plugin._candidates(self.doc, node_editor=True)))

        def test_active_material_does_not_duplicate_adapters(self):
            found = plugin._node_ports([self.mat], self.doc)
            self.assertEqual(sum(n.path == self.adapter.path for n in found), 1)

        def test_graph_revision_counters_are_not_parameters(self):
            before = plugin._snapshot(self.mat)
            self.adapter.SetParameter((self.key, None), self.original + .001, 0)
            after = plugin._snapshot(self.mat)
            self.assertEqual(list(plugin._diff(before, after)), [])
            # These two revision fields were observed in C4D 2027 failure
            # traces. UI commits advance them; isolated SDK writes need not.
            for pid in (526359, 526871):
                self.assertNotIn(pid, after)

        def test_type_and_original_are_preserved(self):
            port = self.graph.GetNode(self.adapter.paths[self.key])
            typ = type(port.GetPortValue())
            param = plugin.Param(self.adapter, self.key, 'r', None)
            param.set(self.adapter, self.original + .001)
            self.assertAlmostEqual(param.get(self.adapter), self.original + .001)
            self.assertIs(type(port.GetPortValue()), typ)
            param.set(self.adapter, self.original)
            self.assertAlmostEqual(param.get(self.adapter), self.original)

        def test_identify_restore_drag_undo_redo(self):
            self.mat.SetBit(c4d.BIT_ACTIVE)
            ctrl = plugin.Controller(lambda: None)
            ctrl.doc = self.doc
            ctrl.cands = [self.mat, self.adapter]
            ctrl.before = [plugin._snapshot(n) for n in ctrl.cands]
            ctrl.noisy = [set(), set()]
            ctrl.undo_before = self.doc.GetUndoPtr()
            ctrl.undo_marks = [(self.mat, False)]
            ctrl.phase = plugin.IDENTIFYING
            ctrl.focus_probe = True
            ctrl.focus_attempt = 1
            ctrl.resume_pt = (0, 0)
            ctrl._redraw = lambda **kwargs: None
            self.doc.StartUndo()
            self.doc.AddUndo(c4d.UNDOTYPE_CHANGE, self.mat)
            self.adapter.SetParameter((self.key, None), self.original + .001, 0)
            self.doc.EndUndo()
            with mock.patch.object(plugin, '_cursor_pos', return_value=(0, 0)), \
                    mock.patch.object(c4d.documents, 'GetActiveDocument', return_value=self.doc):
                ctrl._finish_identify()
            self.assertEqual(ctrl.phase, plugin.LADDER)
            self.assertAlmostEqual(ctrl.param.get(ctrl.targets[0]), self.original)
            ctrl.param.set(ctrl.targets[0], self.original + .1)
            self.assertAlmostEqual(ctrl.param.get(ctrl.targets[0]), self.original + .1)
            self.doc.DoUndo(False)
            mat = self.doc.GetFirstMaterial()
            graph = mat.GetNodeMaterialReference().GetGraph(space)
            path = self.adapter.paths[self.key]
            self.assertAlmostEqual(float(graph.GetNode(path).GetPortValue()), self.original)
            self.doc.DoRedo()
            graph = self.doc.GetFirstMaterial().GetNodeMaterialReference().GetGraph(space)
            self.assertAlmostEqual(float(graph.GetNode(path).GetPortValue()), self.original + .1)

        def test_clones_have_distinct_keys(self):
            other = self.mat.GetClone()
            self.doc.InsertMaterial(other)
            graph = other.GetNodeMaterialReference().GetGraph(space)
            adapter = plugin._NodePorts(other, graph, graph.GetNode(self.adapter.path))
            self.assertFalse(set(self.adapter.vl_snapshot()) & set(adapter.vl_snapshot()))

        def test_all_fixture_real_inputs_roundtrip(self):
            count = 0
            for node in self.nodes:
                adapter = plugin._NodePorts(self.mat, self.graph, node)
                for key, (kind, original) in adapter.vl_snapshot().items():
                    if kind != 'r':
                        continue
                    param = plugin.Param(adapter, key, kind, None)
                    expected = param.set(adapter, original + .001)
                    self.assertAlmostEqual(param.get(adapter), expected)
                    param.set(adapter, original)
                    self.assertAlmostEqual(param.get(adapter), original)
                    count += 1
            self.assertGreater(count, 10)

    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(MaterialChecks))
    print(output.getvalue())
    if not result.wasSuccessful():
        raise AssertionError('Material SDK checks failed')
    return result
