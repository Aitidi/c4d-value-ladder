"""SDK integration checks. Run inside Cinema 4D, not system Python.

main() uses a disposable document without switching the active document.
run_checks(doc, capsule) also accepts a prepared disposable fixture: constant
nodes VL_FLOAT, VL_INT, VL_VECTOR, VL_ARITHMETIC and VL_GROUP/VL_NESTED, plus a capsule
containing VL_FLOAT.
No mouse hook or installed plugin instance is started by these checks.
"""
import importlib.machinery
import importlib.util
import os
import unittest
from unittest import mock

import c4d
import maxon


def _load_plugin():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '..', 'plugin', 'ValueLadder', 'ValueLadder.pyp')
    loader = importlib.machinery.SourceFileLoader('valueladder_checks', path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _options():
    options = maxon.DataDictionary()
    options.Set(maxon.nodes.UndoMode, maxon.nodes.UNDO_MODE.NONE)
    return options


def _graph(owner):
    if isinstance(owner, c4d.documents.BaseDocument):
        owner = owner.FindSceneHook(c4d.SCENENODES_IDS_SCENEHOOK_ID)
    return owner.GetNimbusRef(maxon.NodeSpaceIdentifiers.SceneNodes).GetGraph()


def add_constant(owner, name, datatype, value):
    """One node per transaction; datatype is committed before the value."""
    graph = maxon.GraphDescription.GetGraph(owner)
    with graph.BeginTransaction(_options()) as transaction:
        node = graph.AddChild(maxon.Id(name), maxon.Id('net.maxon.node.type'))
        transaction.Commit()
    with graph.BeginTransaction(_options()) as transaction:
        node.GetInputs().FindChild('datatype').SetPortValue(maxon.Id(datatype))
        transaction.Commit()
    with graph.BeginTransaction(_options()) as transaction:
        node.GetInputs().FindChild('in').SetPortValue(value)
        transaction.Commit()


def run_checks(document, capsule):
    plugin = _load_plugin()

    class SceneNodesChecks(unittest.TestCase):
        def setUp(self):
            self.graph = _graph(document)
            self.owner = document.FindSceneHook(c4d.SCENENODES_IDS_SCENEHOOK_ID)

        def adapter(self, name):
            return plugin._SceneNodePorts(self.owner, self.graph,
                                           self.graph.GetNode(maxon.NodePath(name)))

        def value(self, name):
            # Owner undo replaces the graph. Reacquire it after undo/redo.
            return _graph(document).GetNode(maxon.NodePath(name)).GetInputs().FindChild('in').GetPortValue()

        def test_scalar_and_integer(self):
            for name, kind, number, expected in (('VL_FLOAT', 'r', 1.25, 1.25),
                                                  ('VL_INT', 'l', 9.2, 9)):
                adapter = self.adapter(name)
                key = next(iter(adapter.vl_snapshot()))
                param = plugin.Param(adapter, key, kind, None)
                param.set(adapter, number)
                self.assertEqual(param.get(adapter), expected)
                self.assertEqual(type(self.value(name)).__name__,
                                 'Float64' if kind == 'r' else 'Int64')

        def test_vector_preserves_other_components(self):
            adapter = self.adapter('VL_VECTOR')
            before = adapter.vl_snapshot()
            self.assertEqual(len(before), 1)
            key = next(iter(before))
            original = before[key][1]
            param = plugin.Param(adapter, key, 'v', 1)
            param.set(adapter, original[1] + 2.5)
            self.assertEqual(adapter.vl_snapshot()[key][1],
                             (original[0], original[1] + 2.5, original[2]))
            self.assertEqual(list(plugin._diff(before, adapter.vl_snapshot())), [(key, 'v', 1)])

        def test_graph_keys_do_not_collide(self):
            candidates = plugin._scene_node_ports(document, [capsule])
            self.assertTrue(any(n.material == capsule for n in candidates))
            capsule_graph = _graph(capsule)
            copied = plugin._SceneNodePorts(capsule, capsule_graph,
                capsule_graph.GetNode(maxon.NodePath('VL_FLOAT')))
            self.assertTrue(set(self.adapter('VL_FLOAT').vl_snapshot()).isdisjoint(copied.vl_snapshot()))

        def test_detection_does_not_create_graphs(self):
            plain = c4d.documents.BaseDocument()
            try:
                cube = c4d.BaseObject(c4d.Ocube)
                plain.InsertObject(cube)
                hook = plain.FindSceneHook(c4d.SCENENODES_IDS_SCENEHOOK_ID)
                before = list(hook.GetAllNimbusRefs())
                self.assertEqual(plugin._scene_node_ports(plain, [cube]), [])
                self.assertEqual(hook.GetAllNimbusRefs(), before)
                self.assertEqual(cube.GetAllNimbusRefs(), [])
            finally:
                c4d.documents.KillDocument(plain)

        def test_unset_numeric_input_uses_default_without_writing(self):
            node = self.graph.GetNode(maxon.NodePath('VL_ARITHMETIC'))
            port = node.GetInputs().FindChild('in2')
            self.assertIsNone(port.GetPortValue())
            adapter = self.adapter('VL_ARITHMETIC')
            before = adapter.vl_snapshot()
            key = next(k for k, path in adapter.paths.items() if path == port.GetPath())
            self.assertEqual(before[key], ('r', 0.0))
            self.assertIsNone(port.GetPortValue())
            document.StartUndo()
            document.AddUndo(c4d.UNDOTYPE_CHANGE, self.owner)
            with self.graph.BeginTransaction(_options()) as transaction:
                port.SetPortValue(maxon.Float64(1))
                transaction.Commit()
            document.EndUndo()
            self.assertEqual(list(plugin._diff(before, adapter.vl_snapshot())), [(key, 'r', None)])
            self.assertEqual(adapter.vl_has_scrub_undo(), not adapter.undo_before)
            param = plugin.Param(adapter, key, 'r', None)
            param.set(adapter, 3.5)
            self.assertEqual(param.get(adapter), 3.5)
            document.DoUndo(False)
            restored = _graph(document).GetNode(maxon.NodePath('VL_ARITHMETIC'))
            self.assertIsNone(restored.GetInputs().FindChild('in2').GetPortValue())

        def test_node_editor_does_not_scan_preferences(self):
            with mock.patch.object(plugin, '_prefs_pages', side_effect=AssertionError('Preferences scan')):
                found = plugin._candidates(document, floating=True, node_editor=True)
            self.assertTrue(found)
            self.assertTrue(all(isinstance(n, plugin._NodePorts) for n in found))

        def test_redraw_is_throttled_and_final_refresh_is_forced(self):
            controller = plugin.Controller(lambda: None)
            with mock.patch.object(plugin.time, 'time', side_effect=[10, 10.001, 10.002, 10.003]), \
                    mock.patch.object(c4d, 'DrawViews') as draw, mock.patch.object(c4d, 'EventAdd'):
                controller._redraw()
                controller._redraw()
                controller._redraw()
                controller._redraw(force_event=True)
                self.assertEqual(draw.call_count, 2)

        def test_connected_component_is_protected(self):
            adapter = self.adapter('VL_VECTOR')
            key = next(iter(adapter.vl_snapshot()))
            vector = self.graph.GetNode(maxon.NodePath('VL_VECTOR')).GetInputs().FindChild('in')
            child = next(p for p in vector.GetChildren() if str(p.GetId()).endswith('xin'))
            source = self.graph.GetNode(maxon.NodePath('VL_FLOAT')).GetOutputs().FindChild('out')
            with self.graph.BeginTransaction(_options()) as transaction:
                source.Connect(child)
                transaction.Commit()
            try:
                connections = list(child.GetConnections(maxon.PORT_DIR.INPUT))
                self.assertTrue(connections)
                self.assertNotIn(key, adapter.vl_snapshot())
                self.assertFalse(adapter.SetParameter((key, 1), 123, c4d.DESCFLAGS_SET_NONE))
                self.assertEqual(list(child.GetConnections(maxon.PORT_DIR.INPUT)), connections)
            finally:
                with self.graph.BeginTransaction(_options()) as transaction:
                    maxon.GraphModelHelper.RemoveConnection(source, child)
                    transaction.Commit()

        def test_selected_node_is_discovered(self):
            graph_model = self.graph.GetViewRoot().GetGraph()
            with self.graph.BeginTransaction(_options()) as transaction:
                maxon.GraphModelHelper.DeselectAll(graph_model, maxon.NODE_KIND.NODE)
                maxon.GraphModelHelper.SelectNode(self.graph.GetNode(maxon.NodePath('VL_VECTOR')))
                transaction.Commit()
            try:
                found = plugin._scene_node_ports(document, [])
                self.assertEqual([str(n.path) for n in found], ['VL_VECTOR'])
            finally:
                with self.graph.BeginTransaction(_options()) as transaction:
                    maxon.GraphModelHelper.DeselectAll(graph_model, maxon.NODE_KIND.NODE)
                    transaction.Commit()

        def test_selected_node_inside_group(self):
            node = self.graph.GetNode(maxon.NodePath('VL_GROUP/VL_NESTED'))
            view = self.graph.CreateView(3, node.GetParent().GetPath())
            with self.graph.BeginTransaction(_options()) as transaction:
                maxon.GraphModelHelper.DeselectAll(self.graph.GetViewRoot().GetGraph(), maxon.NODE_KIND.NODE)
                maxon.GraphModelHelper.SelectNode(node)
                transaction.Commit()
            try:
                found = plugin._scene_node_ports(document, [])
                self.assertEqual([str(n.path) for n in found], ['VL_GROUP/VL_NESTED'])
                adapter = found[0]
                key = next(iter(adapter.vl_snapshot()))
                param = plugin.Param(adapter, key, 'r', None)
                param.set(adapter, 3.25)
                self.assertEqual(param.get(adapter), 3.25)
            finally:
                with self.graph.BeginTransaction(_options()) as transaction:
                    # DeselectAll is scoped to a view, just like GetSelectedNodes.
                    maxon.GraphModelHelper.DeselectAll(view.GetViewRoot().GetGraph(), maxon.NODE_KIND.NODE)
                    transaction.Commit()

        def probe(self, focused=False):
            adapter = self.adapter('VL_FLOAT')
            controller = plugin.Controller(lambda: None)
            controller.doc = document
            controller.cands = [adapter]
            controller.before = [adapter.vl_snapshot()]
            controller.noisy = [set()]
            controller.undo_before = document.GetUndoPtr()
            controller.phase = plugin.IDENTIFYING
            controller.focus_probe = focused
            controller.resume_pt = plugin._cursor_pos()
            controller.out_dx = plugin.PX_PER_STEP * 3
            controller._redraw = lambda **kwargs: None
            baseline = float(self.value('VL_FLOAT'))
            document.StartUndo()
            document.AddUndo(c4d.UNDOTYPE_CHANGE, self.owner)
            with self.graph.BeginTransaction(_options()) as transaction:
                self.graph.GetNode(maxon.NodePath('VL_FLOAT')).GetInputs().FindChild('in').SetPortValue(maxon.Float64(baseline + 1))
                transaction.Commit()
            document.EndUndo()
            # Identification normally returns the borrowed mouse pointer.
            # SDK checks must never move the user's real pointer.
            with mock.patch.object(plugin, '_cursor_pos', return_value=controller.resume_pt), \
                    mock.patch.object(c4d.documents, 'GetActiveDocument', return_value=document):
                controller._finish_identify()
            self.assertEqual(controller.scrub_undo, not adapter.undo_before)
            self.assertEqual(float(self.value('VL_FLOAT')), baseline + 3)
            return controller, baseline

        def test_focused_scalar_undo(self):
            document.FlushUndoBuffer()
            controller, baseline = self.probe(focused=True)
            self.assertTrue(controller.focus_probe)
            controller._finish()
            document.DoUndo(False)
            self.assertEqual(float(self.value('VL_FLOAT')), baseline)

        def focused_change(self, adapter, changes):
            controller = plugin.Controller(lambda: None)
            controller.doc = document
            controller.cands = [adapter]
            controller.before = [adapter.vl_snapshot()]
            controller.noisy = [set()]
            controller.undo_before = document.GetUndoPtr()
            controller.phase = plugin.IDENTIFYING
            controller.focus_probe = True
            controller.resume_pt = plugin._cursor_pos()
            controller.out_dx = plugin.PX_PER_STEP * 2
            controller._redraw = lambda **kwargs: None
            document.StartUndo()
            document.AddUndo(c4d.UNDOTYPE_CHANGE, self.owner)
            with self.graph.BeginTransaction(_options()) as transaction:
                for port, value in changes:
                    port.SetPortValue(value)
                transaction.Commit()
            document.EndUndo()
            with mock.patch.object(plugin, '_cursor_pos', return_value=controller.resume_pt), \
                    mock.patch.object(c4d.documents, 'GetActiveDocument', return_value=document):
                controller._finish_identify()
            return controller

        def test_focused_vector_preserves_other_components(self):
            document.FlushUndoBuffer()
            adapter = self.adapter('VL_VECTOR')
            original = self.value('VL_VECTOR')
            port = self.graph.GetNode(maxon.NodePath('VL_VECTOR')).GetInputs().FindChild('in')
            controller = self.focused_change(adapter, [(port, maxon.Vector64(original.x, original.y + .01, original.z))])
            result = self.value('VL_VECTOR')
            self.assertEqual((result.x, result.y, result.z), (original.x, original.y + 2, original.z))
            self.assertEqual(controller.param.comp, 1)
            controller._cancel()
            self.assertEqual(self.value('VL_VECTOR'), original)

        def test_focused_ambiguous_ports_restore_unset_values(self):
            document.FlushUndoBuffer()
            adapter = self.adapter('VL_ARITHMETIC')
            inputs = self.graph.GetNode(maxon.NodePath('VL_ARITHMETIC')).GetInputs()
            a, b = inputs.FindChild('in1'), inputs.FindChild('in2')
            self.assertIsNone(a.GetPortValue())
            self.assertIsNone(b.GetPortValue())
            controller = self.focused_change(adapter, [(a, maxon.Float64(1)), (b, maxon.Float64(2))])
            self.assertEqual(controller.phase, plugin.IDLE)
            inputs = _graph(document).GetNode(maxon.NodePath('VL_ARITHMETIC')).GetInputs()
            self.assertIsNone(inputs.FindChild('in1').GetPortValue())
            self.assertIsNone(inputs.FindChild('in2').GetPortValue())

        def test_one_undo_and_redo(self):
            document.FlushUndoBuffer()
            controller, baseline = self.probe()
            controller._finish()
            document.DoUndo(False)
            self.assertEqual(float(self.value('VL_FLOAT')), baseline)
            document.DoRedo()
            self.assertEqual(float(self.value('VL_FLOAT')), baseline + 3)

        def test_cancel_removes_probe_and_preserves_previous_undo(self):
            document.FlushUndoBuffer()
            controller, baseline = self.probe()
            controller._finish()
            # Reacquire after the preceding test's undo/redo.
            self.graph = _graph(document)
            self.owner = document.FindSceneHook(c4d.SCENENODES_IDS_SCENEHOOK_ID)
            cancelled, start = self.probe()
            cancelled._cancel()
            self.assertEqual(float(self.value('VL_FLOAT')), start)
            document.DoUndo(False)
            self.assertEqual(float(self.value('VL_FLOAT')), start)  # conservative empty native entry
            document.DoUndo(False)
            self.assertEqual(float(self.value('VL_FLOAT')), baseline)

    return unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(SceneNodesChecks))


def main():
    document = c4d.documents.BaseDocument()
    document.SetDocumentName('ValueLadder disposable checks')
    try:
        add_constant(document, 'VL_FLOAT', 'float', maxon.Float64(1))
        add_constant(document, 'VL_INT', 'int', maxon.Int64(7))
        add_constant(document, 'VL_VECTOR', 'net.maxon.parametrictype.vec<3,float>',
                     maxon.Vector64(1, 2, 3))
        graph = _graph(document)
        with graph.BeginTransaction(_options()) as transaction:
            graph.AddChild(maxon.Id('VL_ARITHMETIC'), maxon.Id('net.maxon.node.arithmetic'))
            transaction.Commit()
        add_constant(document, 'VL_NESTED', 'float', maxon.Float64(0))
        graph = _graph(document)
        with graph.BeginTransaction(_options()) as transaction:
            graph.MoveToGroup(maxon.GraphNode(), maxon.Id('VL_GROUP'),
                              [graph.GetNode(maxon.NodePath('VL_NESTED'))])
            transaction.Commit()
        capsule = c4d.BaseObject(180420500)  # Scene Nodes Generator, verified in C4D 2027
        document.InsertObject(capsule)
        add_constant(capsule, 'VL_FLOAT', 'float', maxon.Float64(42))
        return run_checks(document, capsule)
    finally:
        c4d.documents.KillDocument(document)


if __name__ == '__main__':
    main()
