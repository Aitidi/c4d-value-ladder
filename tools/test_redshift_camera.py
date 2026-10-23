"""Redshift Camera SDK audit on inactive disposable copies, without UI input."""
import c4d
import os
import tempfile
from unittest import mock
from test_focus_probe import _load_plugin


def _controller(plugin, temp, node, before, probe):
    ctrl = plugin.Controller(lambda: None)
    ctrl.doc, ctrl.cands, ctrl.before, ctrl.noisy = temp, [node], [before], [set()]
    ctrl.undo_before, ctrl.undo_marks = temp.GetUndoPtr(), [(node, False)]
    ctrl.phase, ctrl.focus_probe = plugin.IDENTIFYING, True
    ctrl.focus_attempt = plugin.FOCUS_PROBES.index(probe) + 1
    ctrl.resume_pt = (0, 0)
    ctrl._redraw = lambda **kwargs: None
    return ctrl


def _identify(plugin, ctrl, temp):
    with mock.patch.object(plugin, '_cursor_pos', return_value=(0, 0)), \
            mock.patch.object(c4d.documents, 'GetActiveDocument', return_value=temp):
        ctrl._finish_identify()


def audit(source, settings=(), context=False, verify=False, rounded=False, only=None):
    plugin = _load_plugin()
    temp = c4d.documents.BaseDocument()
    texture_dir = tempfile.TemporaryDirectory(prefix='valueladder-rs-camera-')
    try:
        temp.GetActiveRenderData().SetData(source.GetDocument().GetActiveRenderData().GetDataInstance().GetClone(c4d.COPYFLAGS_0))
        base = source.GetClone(c4d.COPYFLAGS_0)
        temp.InsertObject(base)
        if context:
            target = c4d.BaseObject(c4d.Onull)
            target.SetAbsPos(c4d.Vector(0, 0, 5000))
            temp.InsertObject(target)
            base[c4d.RSCAMERAOBJECT_TARGETOBJECT] = target
            base[c4d.RSCAMERAOBJECT_FOCUS_AUTO_DISTANCE] = True
            bitmap = c4d.bitmaps.BaseBitmap()
            bitmap.Init(4, 4)
            path = os.path.join(texture_dir.name, 'background.png')
            bitmap.Save(path, c4d.FILTER_PNG)
            image_id = c4d.DescID(c4d.DescLevel(c4d.RSCAMERAOBJECT_BACKGROUND_IMAGE,
                                               c4d.CUSTOMDATATYPE_RSFILE, 0),
                                  c4d.DescLevel(c4d.REDSHIFT_FILE_PATH, c4d.DTYPE_FILENAME, 0))
            base.SetParameter(image_id, path, c4d.DESCFLAGS_SET_NONE)
            assert base.GetParameter(image_id, 0), 'Background fixture path was not accepted'
        for pid, value in settings:
            base.SetParameter(c4d.DescID(c4d.DescLevel(pid)), value,
                              c4d.DESCFLAGS_SET_USERINTERACTION)
            base.GetDescription(c4d.DESCFLAGS_DESC_NONE)
        description = base.GetDescription(c4d.DESCFLAGS_DESC_NONE)
        fields = []
        transforms = (c4d.ID_BASEOBJECT_REL_POSITION, c4d.ID_BASEOBJECT_REL_ROTATION,
                      c4d.ID_BASEOBJECT_REL_SCALE, c4d.ID_BASEOBJECT_FROZEN_POSITION,
                      c4d.ID_BASEOBJECT_FROZEN_ROTATION, c4d.ID_BASEOBJECT_FROZEN_SCALE)
        for bc, did, _ in description:
            ident = bc[c4d.DESC_IDENT] or ''
            if (did.GetDepth() != 1 or not (ident.startswith('RSCAMERAOBJECT_') or did[0].id in transforms)
                    or bc[c4d.DESC_HIDE] or bc[c4d.DESC_CYCLE]):
                continue
            kind = plugin._DESC_KINDS.get(did[0].dtype)
            if kind and did[0].id != c4d.RSCAMERAOBJECT_LUT_LIST:
                components = ((0, 1) if bc[c4d.VECTOR_NO_Z_COMPONENT] else (0, 1, 2))
                fields.extend((did[0].id, kind, comp, ident) for comp in
                              (components if kind == 'v' else (None,)))
        results = []
        for pid, kind, comp, ident in fields:
            if only is not None and pid not in only:
                continue
            node = base.GetClone(c4d.COPYFLAGS_0)
            temp.InsertObject(node)
            try:
                before = plugin._snapshot(node)
                param = plugin.Param(node, pid, kind, comp, snapshot=before)
                original = param.get(node)
                text_original = original
                if rounded:
                    fmt = (c4d.FORMAT_DEGREE if param.unit == c4d.DESC_UNIT_DEGREE else
                           c4d.FORMAT_PERCENT if param.unit == c4d.DESC_UNIT_PERCENT else c4d.FORMAT_FLOAT)
                    text_original = c4d.utils.StringToNumber(
                        c4d.utils.FormatNumber(original, fmt, temp.GetFps()), fmt, temp.GetFps())
                step = 1 if param.is_int else .001
                delta = step / param.scale
                if param.vmax is not None and original + delta > param.vmax:
                    delta = -delta
                probe = ('whole' if param.is_int else 'small', delta * param.scale)
                # Use exact plan literals rather than round-trip scale noise.
                probe = (probe[0], (1 if delta > 0 else -1) * step)
                temp.FlushUndoBuffer()
                ctrl = _controller(plugin, temp, node, before, probe)
                temp.StartUndo()
                temp.AddUndo(c4d.UNDOTYPE_CHANGE, node)
                param.set(node, text_original + delta)
                if pid == c4d.RSCAMERAOBJECT_FOCUS_OFFSET and plugin._rs_linked_focus(node):
                    # Reproduce the AM-only derived-cache update observed in
                    # user testing. SetParameter/ExecutePasses alone does not
                    # reproduce that native UI path on an inactive document.
                    node.GetDataInstance().SetFloat(c4d.RSCAMERAOBJECT_FOCUS_DISTANCE,
                                                   before.focus_distance + delta)
                temp.EndUndo()
                after = plugin._snapshot(node)
                changes = list(plugin._diff(before, after))
                identified = plugin._probe_diff(node, before, after, probe)
                value = param.get(node)
                if verify and changes:
                    _identify(plugin, ctrl, temp)
                    assert ctrl.phase == plugin.LADDER, (ident, comp, list(ctrl.log))
                    assert ctrl.param.pid == pid, (ident, comp, ctrl.param.pid)
                    assert not list(plugin._diff(before, plugin._snapshot(node))), (ident, 'probe restore')
                    ctrl._update((0, 0), plugin.PX_PER_STEP * (1 if delta > 0 else -1))
                    dragged = plugin._snapshot(node)
                    ctrl._finish()
                    temp.DoUndo(False)
                    node = temp.GetFirstObject()
                    assert not list(plugin._diff(before, plugin._snapshot(node))), (ident, 'undo')
                    temp.DoRedo()
                    node = temp.GetFirstObject()
                    assert not list(plugin._diff(dragged, plugin._snapshot(node))), (ident, 'redo')
                    temp.DoUndo(False)
                    node = temp.GetFirstObject()
                    temp.FlushUndoBuffer()
                    ctrl = _controller(plugin, temp, node, before, probe)
                    param = plugin.Param(node, pid, kind, comp, snapshot=before)
                    temp.StartUndo()
                    temp.AddUndo(c4d.UNDOTYPE_CHANGE, node)
                    param.set(node, text_original + delta)
                    if pid == c4d.RSCAMERAOBJECT_FOCUS_OFFSET and plugin._rs_linked_focus(node):
                        node.GetDataInstance().SetFloat(c4d.RSCAMERAOBJECT_FOCUS_DISTANCE,
                                                       before.focus_distance + delta)
                    temp.EndUndo()
                    _identify(plugin, ctrl, temp)
                    assert ctrl.phase == plugin.LADDER, (ident, 'second identify', list(ctrl.log))
                    ctrl._update((0, 0), plugin.PX_PER_STEP * (1 if delta > 0 else -1))
                    ctrl._cancel()
                    node = temp.GetFirstObject()
                else:
                    param.restore(node, original)
                residual = list(plugin._diff(before, plugin._snapshot(node)))
                if verify:
                    assert not residual, (ident, comp, residual)
                    assert before.optics == plugin._snapshot(node).optics, (ident, comp, 'hidden optical state')
                    assert before.focus_distance == plugin._snapshot(node).focus_distance, (ident, comp, 'focus cache')
                results.append(dict(pid=pid, comp=comp, ident=ident, original=original,
                                    value=value, delta=delta, changes=changes,
                                    identified=identified, residual=residual))
            finally:
                node.Remove()
        return results
    finally:
        c4d.documents.KillDocument(temp)
        texture_dir.cleanup()


def enabled_settings():
    names = ('NEAR_CLIPPING_ENABLE', 'FAR_CLIPPING_ENABLE', 'BOKEH_ENABLED', 'TONE_MAPPING_ENABLED',
             'DISPLAY_FOCUS_PLANE', 'DISPLAY_RESOLUTION', 'LAYOUTHELP_DRAW_OVERLAYS',
             'LAYOUTHELP_DRAW_GRID_ENABLE', 'LAYOUTHELP_DRAW_GOLDENSECTION_ENABLE',
             'LAYOUTHELP_DRAW_CROSSHAIR_ENABLE')
    settings = [(getattr(c4d, 'RSCAMERAOBJECT_' + n), True) for n in names]
    settings += [(c4d.RSCAMERAOBJECT_EXPOSURE_TYPE, c4d.RSCAMERAOBJECT_EXPOSURE_TYPE_FILMIC),
                 (c4d.RSCAMERAOBJECT_APERTURE_SHAPE, c4d.RSCAMERAOBJECT_APERTURE_SHAPE_BLADED),
                 (c4d.RSCAMERAOBJECT_SHUTTER_TYPE, c4d.RSCAMERAOBJECT_SHUTTER_TYPE_MOVIE)]
    for name in ('MOTION_BLUR', 'LUT', 'COLOR_CONTROLS', 'BLOOM', 'FLARE', 'STREAK', 'BACKGROUND'):
        settings.append((getattr(c4d, 'RSCAMERAOBJECT_' + name + '_MODE'),
                         c4d.RSCAMERAOBJECT_SWITCH_MODE_ON))
    return tuple(settings)


def run_checks(source):
    plugin = _load_plugin()
    description = source.GetDescription(c4d.DESCFLAGS_DESC_NONE)
    cycles = {did[0].id: [(k, v) for k, v in bc[c4d.DESC_CYCLE] if v and k >= 0]
              for bc, did, _ in description if bc[c4d.DESC_CYCLE]}
    enabled = enabled_settings()
    profiles = [('default', (), False, False, None),
                ('enabled', enabled, False, False, None),
                ('still-manual-shutter', enabled + ((c4d.RSCAMERAOBJECT_SHUTTER_TYPE,
                                                    c4d.RSCAMERAOBJECT_SHUTTER_TYPE_STILL),),
                 False, False, None),
                ('linked-focus-background', enabled, True, False, None),
                ('rounded-editor', enabled, True, True, None)]
    for value, label in cycles[c4d.RSCAMERAOBJECT_PROJECTION]:
        profiles.append(('projection:' + label, enabled + ((c4d.RSCAMERAOBJECT_PROJECTION, value),),
                         True, False, None))
    optics = plugin._rs_optics_ids()
    for lock in (False, True):
        for value, label in cycles[c4d.RSCAMERAOBJECT_SENSOR_SIZE_FIT_MODE]:
            profiles.append(('sensor:' + label + ':lock=' + str(lock),
                             enabled + ((c4d.RSCAMERAOBJECT_SENSOR_SIZE_LOCK_RATIO, lock),
                                        (c4d.RSCAMERAOBJECT_SENSOR_SIZE_FIT_MODE, value)),
                             False, False, optics))
    profiles += [('square-locked', enabled + ((c4d.RSCAMERAOBJECT_SENSOR_SIZE_LOCK_RATIO, True),
                                             (c4d.RSCAMERAOBJECT_SENSOR_SIZE, c4d.Vector(24, 24, 0))),
                  False, False, optics),
                 ('cylindrical-orthographic', enabled + ((c4d.RSCAMERAOBJECT_PROJECTION,
                                                          c4d.RSCAMERAOBJECT_PROJECTION_CYLINDRICAL),
                                                         (c4d.RSCAMERAOBJECT_CYLINDRICAL_ORTHOGRAPHIC, True)),
                  True, False, None)]
    for value, label in cycles[c4d.RSCAMERAOBJECT_BACKGROUND_TYPE]:
        profiles.append(('background:' + label,
                         enabled + ((c4d.RSCAMERAOBJECT_BACKGROUND_TYPE, value),), True, False, None))
    results = []
    for name, settings, context, rounded, only in profiles:
        try:
            rows = audit(source, settings, context=context, verify=True, rounded=rounded, only=only)
        except Exception as error:
            raise AssertionError((name, str(error))) from error
        results.append(dict(profile=name, fields=len(rows), active=sum(bool(r['changes']) for r in rows), rows=rows))
    all_fields = {(r['pid'], r['comp']): r['ident'] for p in results for r in p['rows']}
    covered = {(r['pid'], r['comp']) for p in results for r in p['rows'] if r['changes']}
    assert covered == set(all_fields), {key: name for key, name in all_fields.items() if key not in covered}
    # Two independent optical edits must not be hidden as a single linked edit.
    temp = c4d.documents.BaseDocument()
    try:
        node = source.GetClone(c4d.COPYFLAGS_0)
        temp.InsertObject(node)
        before = plugin._snapshot(node)
        plugin.Param(node, c4d.RSCAMERAOBJECT_FOCAL_LENGTH, 'r', None).set(node, before[500][1] + .001)
        plugin.Param(node, c4d.RSCAMERAOBJECT_SENSOR_SIZE, 'v', 0).set(node, before[7002][1][0] + .001)
        assert len(plugin._probe_diff(node, before, plugin._snapshot(node), ('small', .001))) > 1
    finally:
        c4d.documents.KillDocument(temp)
    # Multi-selection retains a separate optical baseline for every camera.
    temp = c4d.documents.BaseDocument()
    try:
        nodes, baselines = [], []
        for focal in (35.0, 55.0):
            node = source.GetClone(c4d.COPYFLAGS_0)
            temp.InsertObject(node)
            node[c4d.RSCAMERAOBJECT_PROJECTION] = c4d.RSCAMERAOBJECT_PROJECTION_PERSPECTIVE
            node[c4d.RSCAMERAOBJECT_FOCAL_LENGTH] = focal
            nodes.append(node)
            baselines.append(plugin._snapshot(node))
        ctrl = _controller(plugin, temp, nodes[0], baselines[0], ('small', .001))
        ctrl.cands, ctrl.before, ctrl.noisy = nodes, baselines, [set(), set()]
        ctrl.undo_marks = [(node, False) for node in nodes]
        temp.StartUndo()
        for node, before in zip(nodes, baselines):
            temp.AddUndo(c4d.UNDOTYPE_CHANGE, node)
            plugin.Param(node, c4d.RSCAMERAOBJECT_FOCAL_LENGTH, 'r', None).set(node, before[500][1] + .001)
        temp.EndUndo()
        _identify(plugin, ctrl, temp)
        assert ctrl.phase == plugin.LADDER and len(ctrl.targets) == 2, list(ctrl.log)
        for node, before in zip(nodes, baselines):
            assert plugin._snapshot(node).optics == before.optics
        ctrl._update((0, 0), plugin.PX_PER_STEP * 2)
        ctrl._cancel()
        assert sorted(node[c4d.RSCAMERAOBJECT_FOCAL_LENGTH] for node in temp.GetObjects()) == [35.0, 55.0]
    finally:
        c4d.documents.KillDocument(temp)
    return dict(profiles=len(results), cases=sum(p['fields'] for p in results),
                active_cases=sum(p['active'] for p in results), unique_numeric_components=len(covered),
                results=results)


if __name__ == '__main__':
    result = run_checks(c4d.documents.GetActiveDocument().GetActiveObject())
    print({k: v for k, v in result.items() if k != 'results'})
