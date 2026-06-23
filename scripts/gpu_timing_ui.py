# -*- coding: utf-8 -*-
"""在 qrenderdoc 内运行（--ui-python）：从当前已打开抓帧导出 EventId → GPU 耗时（ms）。

须兼容 qrenderdoc 内置 Python 3.6。
由 gpu_timing.py 启动；环境变量 RDC_TIMING_OUT / RDC_TIMING_ERR 指定输出与错误日志。
"""
import os
import sys
import json
import traceback

OUT_PATH = os.environ.get("RDC_TIMING_OUT", "").strip()
ERR_PATH = os.environ.get("RDC_TIMING_ERR", "").strip()
RDC_PATH = os.environ.get("RDC_TIMING_RDC", "").strip()
RUNS = int(os.environ.get("RDC_TIMING_RUNS", "3"))


def write_error(msg):
    print(msg, file=sys.stderr)
    sys.stderr.flush()
    if ERR_PATH:
        try:
            with open(ERR_PATH, "w", encoding="utf-8") as f:
                f.write(msg)
        except OSError:
            pass


def find_pyrenderdoc_context():
    """pyrenderdoc 是 qrenderdoc 注入的全局 CaptureContext，不是可 import 的模块。"""
    if "pyrenderdoc" in globals():
        return globals()["pyrenderdoc"]
    try:
        import __main__
        ctx = getattr(__main__, "pyrenderdoc", None)
        if ctx is not None:
            return ctx
    except Exception:
        pass
    return None


def open_controller_via_renderdoc(rdc_path):
    """无 pyrenderdoc 全局时，用 renderdoc 模块直接打开抓帧（须在 qrenderdoc 进程内）。"""
    import renderdoc as rd

    cap = rd.OpenCaptureFile()
    res = cap.OpenFile(rdc_path, "", None)
    if res != rd.ResultCode.Succeeded:
        raise RuntimeError("OpenCaptureFile.OpenFile failed: {0}".format(res))
    replay = cap.OpenCapture(rd.ReplayOptions(), None)
    if replay is None:
        raise RuntimeError("OpenCapture returned None")
    return replay


def invoke_on_replay_thread(callback):
    """在回放线程上执行回调。"""
    ctx = find_pyrenderdoc_context()
    if ctx is not None:
        ctx.Replay().BlockInvoke(callback)
        return
    rdc = RDC_PATH or os.environ.get("RDC_TIMING_RDC", "").strip()
    if not rdc:
        raise RuntimeError(
            "未找到 pyrenderdoc 全局对象，且未设置 RDC_TIMING_RDC。"
            "请使用: qrenderdoc.exe capture.rdc --ui-python 本脚本.py"
        )
    controller = open_controller_via_renderdoc(os.path.abspath(rdc))
    callback(controller)


def counter_result_seconds(result):
    val = getattr(result, "value", result)
    if hasattr(val, "d"):
        return float(val.d)
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, (list, tuple)) and val:
        return float(val[0])
    return float(val)


def action_chunk_index(action):
    """获取 action 的 chunkIndex（对应 XML 中的事件序号）。"""
    if action is None:
        return None
    events = getattr(action, "events", None) or []
    if not events:
        return None
    return int(events[-1].chunkIndex)


def export_timing_once(controller):
    """执行一次 FetchCounters，返回 {chunk_index: duration_ms}。"""
    import renderdoc as rd

    counter_list = [int(c) for c in controller.EnumerateCounters()]
    event_gpu = int(rd.GPUCounter.EventGPUDuration)
    if event_gpu not in counter_list:
        names = ", ".join(str(c) for c in counter_list[:24])
        raise RuntimeError(
            "当前 GPU/API 回放不支持 EventGPUDuration。"
            " 已枚举部分计数器: " + names
        )

    results = controller.FetchCounters([event_gpu])

    # 构建 eventId -> chunkIndex 映射
    eid_to_chunk = {}

    def walk_actions(action):
        chunk = action_chunk_index(action)
        if chunk is not None:
            for ev in getattr(action, "events", []) or []:
                eid_to_chunk[int(ev.eventId)] = chunk
        for child in getattr(action, "children", []) or []:
            walk_actions(child)

    for root in controller.GetRootActions():
        walk_actions(root)

    # 按 chunkIndex 聚合耗时
    time_by_chunk = {}
    for item in results:
        eid = int(getattr(item, "eventId", 0) or 0)
        if eid <= 0:
            continue
        secs = counter_result_seconds(item)
        if secs > 0:
            chunk = eid_to_chunk.get(eid, eid)
            time_by_chunk[chunk] = secs * 1000.0

    return time_by_chunk


def average_timing_samples(samples):
    """多次采样取平均。"""
    if not samples:
        return {}
    if len(samples) == 1:
        return samples[0]

    all_chunks = set()
    for s in samples:
        all_chunks.update(s.keys())

    result = {}
    for chunk in all_chunks:
        values = [s[chunk] for s in samples if chunk in s]
        if values:
            result[chunk] = sum(values) / len(values)
    return result


def main():
    try:
        samples = []

        def timing_callback(controller):
            for i in range(RUNS):
                timing = export_timing_once(controller)
                samples.append(timing)
                print("run {0}/{1} events={2}".format(i + 1, RUNS, len(timing)), flush=True)

        invoke_on_replay_thread(timing_callback)

        result = average_timing_samples(samples)

        if OUT_PATH:
            out_dir = os.path.dirname(OUT_PATH)
            if out_dir and not os.path.exists(out_dir):
                os.makedirs(out_dir)
            with open(OUT_PATH, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
            print("ok out={0}".format(OUT_PATH), flush=True)
        else:
            print(json.dumps(result, indent=2))

    except Exception as e:
        write_error(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
