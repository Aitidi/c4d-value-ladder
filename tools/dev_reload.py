"""Hot-reload ValueLadder inside a running Cinema 4D without restarting.

Run from the Script Manager (or any Python entry point inside C4D). It stops a
previously loaded dev instance, re-imports ValueLadder.pyp from the repo and
drives it from a small async dialog instead of a registered MessageData plugin.
"""
import importlib.machinery
import importlib.util
import gc
import os

import c4d

# Run it as a file, or set VALUELADDER_TOOLS to this folder when pasting it.
PYP = os.path.join(os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else
                   os.environ["VALUELADDER_TOOLS"],
                   "..", "plugin", "ValueLadder", "ValueLadder.pyp")
DEV_ID = 1000003


class DevHost(c4d.gui.GeDialog):
    def __init__(self, ctrl, version):
        c4d.gui.GeDialog.__init__(self)
        self.ctrl = ctrl
        self.version = version
        self.keep_suspended = False
        self.disposed = False
        self.reload_requested = False

    def CreateLayout(self):
        self.SetTitle("ValueLadder dev " + self.version)
        self.AddStaticText(1, c4d.BFH_SCALEFIT, name="实时测试中 · 正式版已暂停 · 旧探测已关闭")
        self.AddStaticText(2, c4d.BFH_SCALEFIT, name="悬停数值并按住中键，自动选中探测")
        self.GroupBegin(10, c4d.BFH_LEFT, cols=2)
        self.AddButton(100, c4d.BFH_LEFT, name="重新加载代码")
        self.AddButton(101, c4d.BFH_LEFT, name="结束测试")
        self.GroupEnd()
        return True

    def InitValues(self):
        self.SetTimer(250)
        return True

    def Timer(self, msg):
        labels = {0: "悬停数值并按住中键，自动选中探测", 1: "选择档位",
                  2: "正在识别参数", 3: "正在调整数值", 4: "正在自动选中数值"}
        text = labels.get(self.ctrl.phase, "等待操作")
        if self.ctrl.phase == 0 and self.ctrl.log:
            last = self.ctrl.log[-1]
            if "multiple parameters changed" in last:
                text = "识别失败：检测到多个参数变化"
            elif "field could not be identified" in last or "no supported numeric change" in last:
                text = "识别失败：未读到支持的数值变化"
            elif "focus changed" in last:
                text = "识别已退出：输入焦点发生变化"
        self.SetString(2, text)

    def Command(self, id, msg):
        if id in (100, 101) and self.ctrl.phase != 0:
            return True  # finish the gesture before changing its controller
        if id == 100:
            self.reload_requested = True
            c4d.SpecialEventAdd(DEV_ID)
        elif id == 101:
            self.Close()
        return True

    def CoreMessage(self, id, bc):
        if id == DEV_ID:
            if self.reload_requested:
                reload()
                return True
            self.ctrl.pump()
        return c4d.gui.GeDialog.CoreMessage(self, id, bc)

    def AskClose(self):
        if self.ctrl.phase != 0:
            self.ctrl.cancelled = True
            self.ctrl._post("cancel")
            return True
        return False

    def DestroyWindow(self):
        if self.disposed:
            return
        self.disposed = True
        self.ctrl.stop()
        current = getattr(c4d, "_valueladder_dev", None)
        if current and current[0] is self.ctrl:
            c4d._valueladder_dev = None
        if not self.keep_suspended:
            _resume_installed()


def _controllers():
    """C4D's plugin loader need not put the installed module in sys.modules."""
    for item in gc.get_objects():
        if type(item).__name__ != "Controller":
            continue
        code = getattr(getattr(type(item), "stop", None), "__code__", None)
        if code and code.co_filename.lower().endswith("valueladder.pyp"):
            yield item


def _resume_installed():
    suspended = getattr(c4d, "_valueladder_dev_suspended", [])
    c4d._valueladder_dev_suspended = []
    for ctrl, enabled, running in suspended:
        ctrl.enabled = enabled
        if running:
            ctrl.start()


def stop():
    old = getattr(c4d, "_valueladder_dev", None)
    if old:
        if old[0].phase != 0:
            raise RuntimeError("Finish or cancel the middle-mouse gesture before stopping")
        old[0].stop()
        old[1].Close()
        c4d._valueladder_dev = None
    _resume_installed()


def reload(pyp=PYP):
    # Import first: syntax/import errors leave the current instance intact.
    loader = importlib.machinery.SourceFileLoader("valueladder_dev", os.path.normpath(pyp))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    old = getattr(c4d, "_valueladder_dev", None)
    installed = [ctrl for ctrl in _controllers()
                 if (not old or ctrl is not old[0])
                 and ctrl.hook_thread and ctrl.hook_thread.is_alive()]
    if (old and old[0].phase != 0) or any(ctrl.phase != 0 for ctrl in installed):
        raise RuntimeError("Finish or cancel the middle-mouse gesture before reloading")
    if old is not None:
        old[1].keep_suspended = True
        old[0].stop()
        old[1].Close()
        c4d._valueladder_dev = None
    suspended = getattr(c4d, "_valueladder_dev_suspended", [])
    c4d._valueladder_dev_suspended = suspended
    for current in installed:
        suspended.append((current, current.enabled, True))
        current.enabled = False
        current.stop()
    ctrl = mod.Controller(lambda: c4d.SpecialEventAdd(DEV_ID))
    host = DevHost(ctrl, mod.__version__)
    try:
        c4d._valueladder_dev = (ctrl, host, mod)
        if not host.Open(c4d.DLG_TYPE_ASYNC, xpos=40, ypos=40, defaultw=390, defaulth=100):
            raise RuntimeError("Could not open ValueLadder dev host")
        ctrl.start()
    except Exception:
        ctrl.stop()
        host.Close()
        c4d._valueladder_dev = None
        _resume_installed()
        raise
    return ctrl


if __name__ == "__main__":
    reload()


def simulate(x, y, moves, settle=0.5, interval=0.02, start_delay=0.3):
    """Fake a physical middle-button drag: press at (x, y), apply relative moves
    [(dx, dy), ...], release. Runs on a worker thread so C4D keeps processing."""
    import ctypes
    import threading
    import time

    mod = c4d._valueladder_dev[2]

    def send(flags, dx=0, dy=0):
        inp = mod.INPUT(type=0)
        inp.u.mi = mod.MOUSEINPUT(dx, dy, 0, flags, 0, 0)
        mod.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(mod.INPUT))

    log = []

    def run():
        time.sleep(start_delay)
        mod.user32.SetCursorPos(x - 5, y)
        time.sleep(0.05)
        mod.user32.SetCursorPos(x, y)
        time.sleep(0.1)
        send(0x0020)
        time.sleep(settle)
        for dx, dy in moves:
            send(0x0001, dx, dy)
            time.sleep(interval)
        time.sleep(0.3)
        ctrl = c4d._valueladder_dev[0]
        log.append(("before-release", ctrl.phase, ctrl.out_dx, ctrl.pt, ctrl.rung, ctrl.steps))
        send(0x0040)
        time.sleep(0.3)
        log.append(("done", ctrl.phase))

    c4d._valueladder_simlog = log
    threading.Thread(target=run, daemon=True).start()
    return log
