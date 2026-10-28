# -*- coding: utf-8 -*-
"""qrenderdoc helper (Python 3.6+): synchronous replay, atomic evidence output."""
import os
import json
import math
import traceback


def resource_key(rid):
    return str(rid).split('::')[-1]


def primitive_faces(topology, count, instances, restart=False):
    topo = topology.replace('_', '').upper()
    if topo.endswith('TRIANGLELIST'):
        return (count // 3) * instances
    if topo.endswith('TRIANGLESTRIP') and not restart:
        return max(count - 2, 0) * instances
    return None


def collect(controller, runs, include_timing):
    import renderdoc as rd
    resources = controller.GetResources()
    names = {resource_key(r.resourceId): str(r.name) for r in resources}
    shader_names = {resource_key(r.resourceId): str(r.name) for r in resources
                    if r.type == rd.ResourceType.Shader}
    def name(rid, prefix):
        return names.get(resource_key(rid)) or (prefix + ' ' + resource_key(rid))

    structured = controller.GetStructuredFile()
    leaves, draws = [], []
    def walk(action, markers, indirect=False):
        indirect = indirect or bool(action.flags & rd.ActionFlags.Indirect)
        children = list(action.children)
        if not children:
            leaves.append(action)
            if action.flags & rd.ActionFlags.Drawcall:
                draws.append((action, list(markers), indirect))
        next_markers = markers
        if action.flags & rd.ActionFlags.PushMarker:
            next_markers = markers + [str(action.GetName(structured))]
        for child in children:
            walk(child, next_markers, indirect)
    for root in controller.GetRootActions():
        walk(root, [])
    if not draws:
        raise RuntimeError('Replay has no leaf draw actions; refusing empty analysis.')

    result = {'schema_version': 2, 'key_space': 'replay_event', 'status': 'ok',
              'shader_names': shader_names, 'drawcalls': [], 'timing': {},
              'timing_samples': [], 'timing_status': 'skipped', 'diagnostics': [],
              'leaf_actions': [{'event': int(a.eventId), 'flags': str(a.flags),
                               'chunk_index': int(a.events[-1].chunkIndex) if a.events else -1,
                               'draw': bool(a.flags & rd.ActionFlags.Drawcall),
                               'dispatch': bool(a.flags & rd.ActionFlags.Dispatch)} for a in leaves]}
    leaf_ids = set(int(a.eventId) for a in leaves)
    if include_timing:
        try:
            counter = rd.GPUCounter.EventGPUDuration
            if counter not in controller.EnumerateCounters():
                raise RuntimeError('EventGPUDuration is not exposed by this GPU/API.')
            description = controller.DescribeCounter(counter)
            if description.unit != rd.CounterUnit.Seconds:
                raise RuntimeError('Unexpected duration unit: ' + str(description.unit))
            result['counter'] = {'name': str(description.name), 'unit': str(description.unit),
                                 'output_unit': 'ms'}
            for _ in range(runs):
                sample = {}
                for item in controller.FetchCounters([counter]):
                    eid, seconds = int(item.eventId), float(item.value.d)
                    if eid in leaf_ids and math.isfinite(seconds) and seconds >= 0:
                        sample[eid] = seconds * 1000.0
                if not sample or not any(v > 0 for v in sample.values()):
                    raise RuntimeError('No finite positive leaf-event durations returned.')
                result['timing_samples'].append(sample)
            common = set(result['timing_samples'][0])
            for sample in result['timing_samples'][1:]:
                common.intersection_update(sample)
            result['timing'] = {eid: sum(s[eid] for s in result['timing_samples']) / runs
                                for eid in common}
            if not result['timing']:
                raise RuntimeError('GPU samples have no common event IDs.')
            result['timing_status'] = 'ok'
        except Exception:
            result['status'], result['timing_status'] = 'partial', 'failed'
            result['diagnostics'].append(traceback.format_exc())

    for position, (action, markers, indirect) in enumerate(draws, 1):
        controller.SetFrameEvent(action.eventId, True)
        pipe = controller.GetPipelineState()
        topo = str(pipe.GetPrimitiveTopology())
        count, instances = int(action.numIndices), int(action.numInstances)
        faces = primitive_faces(topo, count, instances, pipe.IsRestartEnabled())
        vertex_buffers, index_buffer = pipe.GetVBuffers(), pipe.GetIBuffer()
        depth = pipe.GetDepthTestState()
        vs, ps = pipe.GetShader(rd.ShaderStage.Vertex), pipe.GetShader(rd.ShaderStage.Pixel)
        outputs = [name(t.resource, 'Target') for t in pipe.GetOutputTargets()
                   if t.resource != rd.ResourceId.Null()]
        fields = dict(event=int(action.eventId), event_space='replay_event',
                      chunk_index=int(action.events[-1].chunkIndex) if action.events else -1,
                      draw_type=('DrawIndexed' if action.flags & rd.ActionFlags.Indexed else 'Draw')
                                + ('Indirect' if indirect else ''),
                      mesh_type='', asset_module='', asset_module_source='', resource_type='',
                      resource_category='', resource_path='', matched_mesh_name='',
                      resource_match_source='', mesh_key='', marker=' / '.join(markers) or '(no marker)',
                      pass_name=markers[-1] if markers else '(no marker)', topology=topo,
                      face_estimate=str(faces) if faces is not None else '', face_known=faces is not None,
                      face_source='replay_action' if faces is not None else 'unknown',
                      indirect_args_decoded=indirect, ib_capacity_faces_estimate='',
                      combined_faces_estimate=str(faces) if faces is not None else '',
                      index_or_vertex_count=count, instance_count=instances,
                      vb0=name(vertex_buffers[0].resourceId, 'Buffer') if vertex_buffers else '(no VB)',
                      ib=name(index_buffer.resourceId, 'Buffer'),
                      vs=name(vs, 'VS'), ps=name(ps, 'PS'), raw_ps=name(ps, 'PS'),
                      blend=any(b.enabled for b in pipe.GetColorBlends()),
                      depth_write=bool(depth.depthWrites), depth_enable=bool(depth.depthEnable),
                      rt=', '.join(outputs) or '(no color RT)')
        result['drawcalls'].append(fields)
        if position % 100 == 0:
            print('Replay states: {0}/{1}'.format(position, len(draws)), flush=True)
    return result


def main():
    import renderdoc as rd
    capture, controller = None, None
    result = {'schema_version': 2, 'key_space': 'replay_event', 'status': 'failed',
              'timing_status': 'failed', 'timing': {}, 'drawcalls': [], 'diagnostics': []}
    source, target = os.path.abspath(os.environ['RDC_TIMING_RDC']), os.environ['RDC_TIMING_OUT']
    try:
        capture = rd.OpenCaptureFile()
        status = capture.OpenFile(source, '', None)
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError('OpenFile: ' + str(status))
        status, controller = capture.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded or controller is None:
            raise RuntimeError('OpenCapture: ' + str(status))
        result = collect(controller, max(1, int(os.environ.get('RDC_TIMING_RUNS', '3'))),
                         os.environ.get('RDC_INCLUDE_TIMING', '1') != '0')
    except Exception:
        result['diagnostics'].append(traceback.format_exc())
    finally:
        if controller is not None:
            controller.Shutdown()
        if capture is not None:
            capture.Shutdown()
    result['source'] = {'path': source, 'size': os.path.getsize(source),
                        'mtime_ns': os.stat(source).st_mtime_ns}
    with open(target + '.tmp', 'w', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    os.replace(target + '.tmp', target)
    # Dedicated helper process, never the user's existing RenderDoc session.
    os._exit(0 if result['status'] == 'ok' else 2)


if __name__ == '__main__':
    main()
