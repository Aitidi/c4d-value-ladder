"""Hot-reload ValueLadder inside a running Cinema 4D without restarting.

Run from the Script Manager (or any Python entry point inside C4D). It stops a
previously loaded dev instance, re-imports ValueLadder.pyp from the repo and
drives it from a small async dialog instead of a registered MessageData plugin.
"""
import importlib.machinery
import importlib.util
import os

import c4d

PYP = os.path.join(os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else
                   r"D:\AgentProject\Cinema 4D\ValueLadder\tools",
                   "..", "plugin", "ValueLadder", "ValueLadder.pyp")
DEV_ID = 1000003


class DevHost(c4d.gui.GeDialog):
    def __init__(self, ctrl):
        c4d.gui.GeDialog.__init__(self)
        self.ctrl = ctrl

    def CreateLayout(self):
        self.SetTitle("ValueLadder dev")
        self.AddStaticText(1, c4d.BFH_SCALEFIT, name="ValueLadder dev host running")
        return True

    def CoreMessage(self, id, bc):
        if id == DEV_ID:
            self.ctrl.pump()
        return c4d.gui.GeDialog.CoreMessage(self, id, bc)


def reload(pyp=PYP):
    old = getattr(c4d, "_valueladder_dev", None)
    if old is not None:
        try:
            old[0].stop()
            old[1].Close()
        except Exception:
            pass
    loader = importlib.machinery.SourceFileLoader("valueladder_dev", os.path.normpath(pyp))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    ctrl = mod.Controller(lambda: c4d.SpecialEventAdd(DEV_ID))
    host = DevHost(ctrl)
    host.Open(c4d.DLG_TYPE_ASYNC, xpos=40, ypos=40, defaultw=260, defaulth=30)
    ctrl.start()
    c4d._valueladder_dev = (ctrl, host, mod)
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
        log.append(("before-release", ctrl.phase, ctrl.dx, ctrl.dy, ctrl.rung, ctrl.steps))
        send(0x0040)
        time.sleep(0.3)
        log.append(("done", ctrl.phase))

    c4d._valueladder_simlog = log
    threading.Thread(target=run, daemon=True).start()
    return log
