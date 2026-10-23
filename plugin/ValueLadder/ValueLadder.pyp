"""ValueLadder - Houdini-style middle-mouse "ladder" value scrubbing for Cinema 4D.

Hold the middle mouse button over a numeric field in the Attribute Manager (or
any other manager that shows parameter fields), move up/down to pick a step
size, then move left/right to change the value. Release to commit (one undo
step), right-click while holding to cancel.

How it works (Windows only):
  1. Middle-button down over a numeric field automatically focuses it with a
     left click and commits pending text before recording the baseline.
  2. Show the ladder and try +/-0.001, +/-1, then native Up/Down steps.
     Identify the changed numeric parameter from snapshots and restore it.
  3. Over the ladder, vertical motion chooses a rung; beside it, horizontal
     travel adjusts the parameter through its API. Native input supplies the
     gesture undo. The older simulated mouse-drag probe remains disabled.
"""
import ctypes
import ctypes.wintypes as wt
import math
import os
import sys
import threading
import time
from collections import deque

import c4d
import maxon

__version__ = "0.8.12"

# Maxon reserves 1000001-1000010 for testing. Replace with IDs registered at
# https://developers.maxon.net before a public release.
PLUGIN_ID = 1000001
COMMAND_ID = 1000002
PREFS_ID = 1000004      # 1000003 is taken by tools/dev_reload.py

# Preferences page (res/description/valueladderprefs.*). The values live in a
# sub-container of the world container, keyed by PLUGIN_ID, so they persist.
PREFS_DESCRIPTION = "valueladderprefs"
PREFS_ENABLE_AT_STARTUP = 1000

# Ladder behaviour.
RUNGS = [1000.0, 100.0, 10.0, 1.0, 0.1, 0.01, 0.001]
INT_LAST_RUNG = RUNGS.index(1.0)   # integers stop at a step of 1
PX_PER_STEP = 6         # pixels of horizontal travel per step (native scrub: ~2)
SCRUB_PX = 8            # native scrub that reveals the parameter (~4 native steps)
FOCUS_PROBES = (("small", 0.001), ("small", -0.001),
                ("whole", 1), ("whole", -1), ("native", 1), ("native", -1))
ENABLE_LEGACY_PROBE = False  # automatic focus uses numeric probes, never native drag
SCRUB_TRIES = (1, 1, -1)  # direction of each try, relative to the user's drag
MAX_CANDIDATES = 500    # nodes snapshotted per gesture
EVENT_INTERVAL = 0.1    # seconds between manager refreshes while scrubbing
DRAW_INTERVAL = 1.0 / 30.0  # avoid redrawing a heavy scene for every mouse event
OVERLAY_W = 52          # width of the ladder, in pixels (Houdini-like)
RUNG_PX = OVERLAY_W     # rungs are square
EDGE_PX = 1             # how close to a screen edge the pointer wraps
WARP_IN_PX = 1          # where it comes back in: this far inside the other edge

IS_WINDOWS = sys.platform.startswith("win")

# Set VALUELADDER_DEBUG=1 to trace every step to %TEMP%\valueladder_debug.log.
DEBUG_LOG = (os.path.join(os.environ.get("TEMP", "."), "valueladder_debug.log")
             if os.environ.get("VALUELADDER_DEBUG") else None)


# ---------------------------------------------------------------------------
# Win32 plumbing
# ---------------------------------------------------------------------------
if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32")
    kernel32 = ctypes.WinDLL("kernel32")

    LRESULT = ctypes.c_ssize_t
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                    ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD),
                    ("flags", wt.DWORD), ("time", wt.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                    ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                    ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                    ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]

    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wt.WPARAM, wt.LPARAM]
    user32.CallNextHookEx.restype = LRESULT
    user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
    user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    user32.IsWindowVisible.argtypes = [wt.HWND]
    user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = wt.LONG
    user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, wt.LONG]
    user32.SetWindowLongW.restype = wt.LONG
    user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wt.UINT]
    user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
    user32.CreateWindowExW.restype = wt.HWND
    user32.DestroyWindow.argtypes = [wt.HWND]
    user32.UpdateLayeredWindow.argtypes = [wt.HWND, ctypes.c_void_p,
        ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE), ctypes.c_void_p,
        ctypes.POINTER(wt.POINT), wt.DWORD, ctypes.c_void_p, wt.DWORD]
    user32.FillRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT), ctypes.c_void_p]
    user32.DrawTextW.argtypes = [ctypes.c_void_p, wt.LPCWSTR, ctypes.c_int,
                               ctypes.POINTER(wt.RECT), wt.UINT]
    gdi32.CreateSolidBrush.argtypes = [wt.DWORD]
    gdi32.CreateSolidBrush.restype = ctypes.c_void_p
    gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wt.DWORD] * 8 + [wt.LPCWSTR]
    gdi32.CreateFontW.restype = ctypes.c_void_p
    gdi32.SetBkMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
    gdi32.SetTextColor.argtypes = [ctypes.c_void_p, wt.DWORD]
    user32.GetForegroundWindow.restype = wt.HWND
    user32.SetForegroundWindow.argtypes = [wt.HWND]
    user32.LoadCursorW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
    user32.LoadCursorW.restype = ctypes.c_void_p
    user32.CopyIcon.argtypes = [ctypes.c_void_p]
    user32.CopyIcon.restype = ctypes.c_void_p
    user32.SetSystemCursor.argtypes = [ctypes.c_void_p, wt.DWORD]
    user32.SystemParametersInfoW.argtypes = [wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT]
    user32.ShowCursor.argtypes = [wt.BOOL]
    user32.ShowCursor.restype = ctypes.c_int
    user32.GetDpiForWindow.argtypes = [wt.HWND]
    user32.GetDpiForWindow.restype = wt.UINT
    user32.WindowFromPoint.argtypes = [wt.POINT]
    user32.WindowFromPoint.restype = wt.HWND
    user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
    user32.GetAncestor.restype = wt.HWND
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
    user32.MapVirtualKeyW.restype = wt.UINT
    user32.GetDC.argtypes = [wt.HWND]
    user32.GetDC.restype = ctypes.c_void_p
    user32.ReleaseDC.argtypes = [wt.HWND, ctypes.c_void_p]
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, wt.DWORD]
    gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.UINT, wt.UINT,
                                ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]

WH_MOUSE_LL = 14
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
WM_QUIT = 0x0012
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_MOUSEWHEEL = 0x020A
GA_ROOTOWNER = 3
GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_POPUP = -0x80000000
WS_FRAME_BITS = 0x00C00000 | 0x00040000 | 0x00080000 | 0x00020000 | 0x00010000  # caption, sizebox, sysmenu, min/max
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x00080000, 0x00000020
ULW_OPAQUE = 0x00000004
SPI_SETCURSORS = 0x0057
HWND_TOPMOST = wt.HWND(-1) if sys.platform.startswith("win") else None
SWP_SHOWWINDOW, SWP_NOACTIVATE = 0x0040, 0x0010
SRCCOPY = 0x00CC0020

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_ESCAPE, VK_RETURN, VK_TAB, VK_END = 0x1B, 0x0D, 0x09, 0x23
VK_UP, VK_DOWN = 0x26, 0x28
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

# Marks input we synthesise ourselves, so the hook lets it through.
INJECT_MAGIC = 0x56414C44  # "VALD"


def _send_mouse(flags, x=0, y=0):
    if flags & MOUSEEVENTF_ABSOLUTE:
        vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = max(1, user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) - 1)
        vh = max(1, user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) - 1)
        x = int(round((x - vx) * 65535.0 / vw))
        y = int(round((y - vy) * 65535.0 / vh))
        flags |= MOUSEEVENTF_VIRTUALDESK
    inp = INPUT(type=INPUT_MOUSE)
    inp.u.mi = MOUSEINPUT(x, y, 0, flags, 0, INJECT_MAGIC)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def _send_key(vk, hold=0.0):
    extended = KEYEVENTF_EXTENDEDKEY if vk in (VK_END, VK_UP, VK_DOWN) else 0
    for flags in (0, KEYEVENTF_KEYUP):
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.u.ki = KEYBDINPUT(vk, user32.MapVirtualKeyW(vk, 0), flags | extended, 0, INJECT_MAGIC)
        if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) != 1:
            raise RuntimeError("numeric probe key was not delivered")
        if hold and not flags:
            time.sleep(hold)


def _send_text(text):
    """Send the short numeric expression without using the clipboard."""
    for char in text:
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            inp = INPUT(type=INPUT_KEYBOARD)
            inp.u.ki = KEYBDINPUT(0, ord(char), flags, 0, INJECT_MAGIC)
            if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) != 1:
                raise RuntimeError("numeric probe text was not delivered")
        time.sleep(0.006)


def _send_probe_step(kind, value):
    if kind == "small":
        _send_text(format(value, "+.3f"))
    elif kind == "whole":
        _send_text(format(int(value), "+d"))
    elif kind == "native":
        _send_key(VK_UP if value > 0 else VK_DOWN, hold=0.03)
    else:
        raise ValueError("unknown focus probe kind")


def _move_to(x, y):
    _send_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, x, y)


def _cursor_pos():
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def _grab_screen(x, y, w, h):
    """Return a BGRA byte buffer of a screen rectangle."""
    hdc = user32.GetDC(None)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    gdi32.BitBlt(mdc, 0, 0, w, h, hdc, x, y, SRCCOPY)
    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth, bmi.biHeight = w, -h
    bmi.biPlanes, bmi.biBitCount = 1, 32
    buf = (ctypes.c_ubyte * (w * h * 4))()
    gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    gdi32.SelectObject(mdc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(None, hdc)
    return buf


# Standard cursors swapped for the left-right arrow while the ladder is up.
# Cinema 4D sets its cursor per gadget, so a per-window SetCursor would
# flicker; swapping the system cursors doesn't. SPI_SETCURSORS puts the
# user's cursor scheme back.
OCR_SIZEWE_ID = 32644
_SWAPPED_CURSORS = (32512, 32513, 32515, 32649, 32646, 32645, 32642, 32643)
#                   arrow  ibeam  cross  hand   sizeall sizens nwse  nesw


def _cursors_scrub(on):
    if on:
        src = user32.LoadCursorW(None, ctypes.c_void_p(OCR_SIZEWE_ID))
        for cid in _SWAPPED_CURSORS:
            user32.SetSystemCursor(user32.CopyIcon(src), cid)
    else:
        user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)


def _gui_rgb(color_id):
    v = c4d.gui.GetGuiWorldColor(color_id)
    return (int(round(v.x * 255)), int(round(v.y * 255)), int(round(v.z * 255)))


# ---------------------------------------------------------------------------
# Field detection (runs on the hook thread, must be fast)
# ---------------------------------------------------------------------------
class FieldDetector(object):
    WIDTH, HEIGHT = 640, 72

    def __init__(self):
        self.edit_colors = []
        self.refresh_colors()

    def refresh_colors(self):
        """Read the theme colours on the main thread."""
        colors = []
        for cid in ("COLOR_BGEDIT", "COLOR_BGFOCUS"):
            if hasattr(c4d, cid):
                try:
                    colors.append(_gui_rgb(getattr(c4d, cid)))
                except Exception:
                    pass
        self.edit_colors = colors or [(28, 28, 28)]

    def _is_edit(self, buf, x, y):
        i = (y * self.WIDTH + x) * 4
        b, g, r = buf[i], buf[i + 1], buf[i + 2]
        for (er, eg, eb) in self.edit_colors:
            if abs(r - er) <= 3 and abs(g - eg) <= 3 and abs(b - eb) <= 3:
                return True
        return False

    def check(self, px, py):
        """Return ("field", left edge x, centre y) in screen pixels, or (reason, None, None)."""
        w, h = self.WIDTH, self.HEIGHT
        x0, y0 = px - w // 2, py - h // 2
        buf = _grab_screen(x0, y0, w, h)
        cx, cy = px - x0, py - y0
        span = range(max(0, cx - 40), min(w, cx + 41))

        def coverage(y):
            return sum(1 for x in span if self._is_edit(buf, x, y)) / float(len(span))

        # Rows of the field around the cursor: text makes some rows patchy, the
        # thin gaps between stacked fields have no edit colour at all.
        if coverage(cy) < 0.25:
            return "no edit colour", None, None
        top = cy
        while top > 0 and coverage(top - 1) >= 0.25:
            top -= 1
        bottom = cy
        while bottom < h - 1 and coverage(bottom + 1) >= 0.25:
            bottom += 1
        if top == 0 or bottom == h - 1:
            return "unbounded", None, None
        if not (8 <= bottom - top + 1 <= 48):
            return "height %d" % (bottom - top + 1), None, None

        # The padding rows next to the border are clean; measure the width there.
        widest = 0
        field_left = None
        for y in (top, top + 1, top + 2, bottom - 2, bottom - 1, bottom):
            if not self._is_edit(buf, cx, y):
                continue
            left = cx
            while left > 0 and self._is_edit(buf, left - 1, y):
                left -= 1
            right = cx
            while right < w - 1 and self._is_edit(buf, right + 1, y):
                right += 1
            if right - left + 1 > widest:
                widest, field_left = right - left + 1, left
        if not 20 <= widest <= 520:
            return "width %d" % widest, None, None
        return "field", x0 + field_left, y0 + (top + bottom) // 2


# ---------------------------------------------------------------------------
# Parameter snapshot / diff (main thread)
# ---------------------------------------------------------------------------
_VCOMP = (c4d.VECTOR_X, c4d.VECTOR_Y, c4d.VECTOR_Z)


_DESC_KINDS = {c4d.DTYPE_REAL: "r", c4d.DTYPE_LONG: "l", c4d.DTYPE_VECTOR: "v",
               c4d.DTYPE_TIME: "t"}
_KIND_DTYPES = {"r": c4d.DTYPE_REAL, "l": c4d.DTYPE_LONG, "v": c4d.DTYPE_VECTOR,
                "t": c4d.DTYPE_TIME}
MAX_SHADERS = 200       # shaders, layers or keys gathered per gesture
MAX_ATTACHED_TAGS = 200  # tags exposed through selected objects' Attribute Manager tabs
MAX_FIELD_OWNERS = 50   # selected nodes searched for field lists
MAX_MATERIALS = 64     # material selection discovery per gesture
MATERIAL_SCAN_BUDGET = 0.020  # seconds searching other materials for selected nodes
MAX_GRAPH_NODES = 30    # node-material nodes read per material
MAX_SCENE_PORTS = 512   # bound nested Scene Nodes port scans per node
MAX_SCENE_SELECTION_NODES = 512  # bound selection search inside groups
KEY_SCAN_BUDGET = 0.015 # seconds spent looking for selected keys


def _fps(node):
    try:
        doc = node if isinstance(node, c4d.documents.BaseDocument) else node.GetDocument()
        if doc is None:
            doc = c4d.documents.GetActiveDocument()
        return doc.GetFps()
    except Exception:
        return 30


class _CameraSnapshot(dict):
    pass


def _snapshot(node):
    """Every numeric parameter of node, keyed by its id (an int) or, for
    nested parameters such as user data, by the tuple of level ids."""
    if hasattr(node, "vl_snapshot"):
        try:
            return node.vl_snapshot()
        except Exception:
            return {}  # a graph or selected node can disappear between events
    snap = _CameraSnapshot() if _is_rs_camera(node) else {}
    try:
        data = node.GetDataInstance()
    except Exception:
        data = None
    # Node materials store graph revision counters as plain integers. They
    # change with any port edit and must not compete with actual port diffs.
    # Only public description parameters belong in their classic snapshot.
    if (isinstance(node, c4d.BaseMaterial) and node.IsNodeBased()) or _is_rs_camera(node):
        data = None
    if data is not None:
        i = 0
        while True:
            pid = data.GetIndexId(i)
            if pid == c4d.NOTOK:
                break
            i += 1
            t = data.GetType(pid)
            if t == c4d.DA_REAL:
                snap[pid] = ("r", data.GetFloat(pid))
            elif t == c4d.DA_LONG:
                snap[pid] = ("l", data.GetInt32(pid))
            elif t == c4d.DA_VECTOR:
                v = data.GetVector(pid)
                snap[pid] = ("v", (v.x, v.y, v.z))
    # Many parameters live outside the container (project settings, user
    # data, values a plugin computes); the description lists them all.
    _snapshot_description(node, snap)
    if isinstance(snap, _CameraSnapshot):
        # Keep optical backing values for restoration even when a fit mode
        # hides a sensor gadget. They are not extra discovery candidates.
        snap.optics = {}
        for pid, kind in ((c4d.RSCAMERAOBJECT_SENSOR_SIZE, "v"),
                          (c4d.RSCAMERAOBJECT_SENSOR_SIZE_PRESET, "l"),
                          (c4d.RSCAMERAOBJECT_FOCAL_LENGTH, "r")):
            value = node.GetParameter(_descid(pid, kind), c4d.DESCFLAGS_GET_NONE)
            snap.optics[pid] = (kind, (value.x, value.y, value.z) if kind == "v" else value)
        snap.focus_distance = node.GetParameter(_descid(c4d.RSCAMERAOBJECT_FOCUS_DISTANCE, "r"),
                                               c4d.DESCFLAGS_GET_NONE)
    return snap


def _snapshot_description(node, snap):
    try:
        desc = node.GetDescription(c4d.DESCFLAGS_DESC_NONE)
        entries = iter(desc)
    except Exception:
        return
    while True:
        try:
            bc, descid, _ = next(entries)
        except StopIteration:
            return
        except Exception:
            return   # some plugins' descriptions can't be walked from Python
        depth = descid.GetDepth()
        if depth == 1:
            key = descid[0].id
        elif depth == 2 and descid[0].id == c4d.ID_USERDATA:
            key = (descid[0].id, descid[1].id)
        else:
            continue
        # PSR descriptions also expose hidden global/absolute values. One
        # visible edit changes those derived values too; they are not separate
        # editable targets. Remove them even if the container supplied them.
        if (bc[c4d.DESC_HIDE] or (_is_rs_camera(node)
                and key == c4d.RSCAMERAOBJECT_FOCUS_DISTANCE and _rs_linked_focus(node))):
            snap.pop(key, None)
            continue
        kind = _DESC_KINDS.get(descid[depth - 1].dtype)
        if kind is None or (key in snap and not _is_rs_camera(node)):
            continue
        try:
            v = node.GetParameter(descid, c4d.DESCFLAGS_GET_NONE)
            if kind == "t":
                # Keep the exact rational time. FPS edits change frame
                # readouts, and rebuilding from float seconds rounds to ms.
                snap[key] = ("t", v)
            elif kind == "v":
                z = 0.0 if _is_rs_camera(node) and bc[c4d.VECTOR_NO_Z_COMPONENT] else v.z
                snap[key] = ("v", (v.x, v.y, z))
            elif kind == "r":
                snap[key] = ("r", float(v))
            else:
                snap[key] = ("l", int(v))
        except Exception:
            pass


def _is_rs_camera(node):
    return isinstance(node, c4d.BaseObject) and node.GetType() == c4d.Orscamera


def _rs_linked_focus(node):
    return bool(node[c4d.RSCAMERAOBJECT_TARGETOBJECT]
                or node[c4d.RSCAMERAOBJECT_FOCUS_AUTO_DISTANCE])


def _diff(before, after):
    """Yield (param key, kind, component index or None) for changed values."""
    for key, (kind, new) in after.items():
        old = before.get(key)
        if old is None or old[0] != kind:
            continue
        old = old[1]
        if kind == "v":
            for c in range(3):
                if abs(new[c] - old[c]) > 1e-9:
                    yield key, kind, c
        elif kind == "t":
            if abs(new.Get() - old.Get()) > 1e-9:
                yield key, kind, None
        elif abs(new - old) > 1e-9:
            yield key, kind, None


_PROJECT_RANGES = (c4d.DOCUMENT_MINTIME, c4d.DOCUMENT_MAXTIME,
                   c4d.DOCUMENT_LOOPMINTIME, c4d.DOCUMENT_LOOPMAXTIME)


def _rs_optics_ids():
    return (c4d.RSCAMERAOBJECT_SENSOR_SIZE, c4d.RSCAMERAOBJECT_SENSOR_SIZE_PRESET,
            c4d.RSCAMERAOBJECT_FOCAL_LENGTH, c4d.RSCAMERAOBJECT_FOV,
            c4d.RSCAMERAOBJECT_SENSOR_SIZE_FLOAT)


def _rs_restore_optics(node, snapshot):
    node.GetDescription(c4d.DESCFLAGS_DESC_NONE)
    snapshot = getattr(snapshot, "optics", snapshot)
    for pid in _rs_optics_ids()[:3]:
        kind, value = snapshot[pid]
        descid = _descid(pid, kind)
        current = node.GetParameter(descid, c4d.DESCFLAGS_GET_NONE)
        if ((current.x, current.y, current.z) if kind == "v" else current) != value:
            node.SetParameter(descid, c4d.Vector(*value) if kind == "v" else value,
                              c4d.DESCFLAGS_SET_USERINTERACTION | c4d.DESCFLAGS_SET_DONTCHECKMINMAX)
    node.GetDescription(c4d.DESCFLAGS_DESC_NONE)


def _rs_probe_diff(node, before, after, changed, probe):
    # A known expression delta distinguishes a typed lens/FOV/sensor edit
    # from its derived values. Replay on an inactive copy verifies the entire
    # change footprint; a whitelist alone must never hide independent edits.
    group = _rs_optics_ids()
    if (probe is None or probe[0] not in ("small", "whole") or not changed
            or not hasattr(before, "optics")
            or any(pid not in group for pid, _, _ in changed)):
        return changed
    matches = []
    for key in changed:
        pid, kind, comp = key
        if pid == c4d.RSCAMERAOBJECT_SENSOR_SIZE_PRESET:
            continue
        old, new = before[pid][1], after[pid][1]
        if comp is not None:
            old, new = old[comp], new[comp]
        scale = 180.0 / math.pi if pid == c4d.RSCAMERAOBJECT_FOV else 1.0
        bases = [old]
        # Entering text editing can start from the displayed, rounded value.
        # Match that exact SDK formatting too; do not widen the tolerance and
        # accidentally accept a different coupled field with a similar delta.
        try:
            fmt = c4d.FORMAT_DEGREE if pid == c4d.RSCAMERAOBJECT_FOV else c4d.FORMAT_FLOAT
            text = c4d.utils.FormatNumber(old, fmt, _fps(node))
            bases.append(c4d.utils.StringToNumber(text, fmt, _fps(node)))
        except Exception:
            pass
        if any(abs((new - base) * scale - probe[1]) <= max(1e-7, abs(probe[1]) * 1e-4)
               for base in bases):
            matches.append(key)
    if not matches:
        return changed
    temp = c4d.documents.BaseDocument()
    try:
        owner_doc = node.GetDocument()
        if owner_doc is not None:
            temp.GetActiveRenderData().SetData(owner_doc.GetActiveRenderData().GetDataInstance().GetClone(c4d.COPYFLAGS_0))
        verified = []
        for key in matches:
            clone = node.GetClone(c4d.COPYFLAGS_0)
            temp.InsertObject(clone)
            try:
                _rs_restore_optics(clone, before)
                if list(_diff(before, _snapshot(clone))):
                    continue
                pid, kind, comp = key
                target = after[pid][1]
                if comp is not None:
                    target = target[comp]
                Param(clone, pid, kind, comp).set(clone, target)
                if not list(_diff(after, _snapshot(clone))):
                    verified.append(key)
            finally:
                clone.Remove()
        if len(verified) == 1:
            return verified
        # Equal XY dimensions under ratio lock have identical effects and
        # identical display steps. Treat them as one shared size control.
        if (len(verified) == 2 and all(k[0] == group[0] for k in verified)
                and node[c4d.RSCAMERAOBJECT_SENSOR_SIZE_LOCK_RATIO]
                and abs(before[group[0]][1][0] - before[group[0]][1][1]) < 1e-9):
            return verified[:1]
    except Exception:
        return changed
    finally:
        c4d.documents.KillDocument(temp)
    return changed


def _probe_diff(node, before, after, probe=None):
    """Recognize only documented range clamps, leaving ambiguity intact."""
    changed = list(_diff(before, after))
    if _is_rs_camera(node) and len(changed) > 1:
        return _rs_probe_diff(node, before, after, changed, probe)
    if not isinstance(node, c4d.documents.BaseDocument):
        return changed
    keys = {key for key, _, _ in changed}
    lo, hi, preview_lo, preview_hi = _PROJECT_RANGES
    for primary, secondary, clamp in ((lo, (preview_lo, preview_hi), max),
                                       (hi, (preview_lo, preview_hi), min)):
        if primary not in keys or not keys.issubset({primary, *secondary}):
            continue
        if not all(key in before and key in after for key in _PROJECT_RANGES):
            continue
        if after[lo][1] > after[hi][1]:
            continue
        bound = after[primary][1].Get()
        old_bound = before[primary][1].Get()
        linked = preview_lo if primary == lo else preview_hi
        def expected(key):
            value = before[key][1].Get()
            # An endpoint pinned to the project boundary follows it during
            # expansion too; an interior endpoint only moves when clamped.
            return bound if key == linked and abs(value - old_bound) < 1e-9 else clamp(value, bound)
        if all(abs(after[key][1].Get() - expected(key)) < 1e-9 for key in secondary):
            return [entry for entry in changed if entry[0] == primary]
    return changed


def _walk(node):
    """node and the siblings and children after it."""
    while node is not None:
        yield node
        for child in _walk(node.GetDown()):
            yield child
        node = node.GetNext()


def _shaders(owners):
    found = []
    for owner in owners:
        try:
            first = owner.GetFirstShader()
        except Exception:
            continue
        for sh in _walk(first):
            found.append(sh)
            if len(found) >= MAX_SHADERS:
                return found
    return found


def _attached_tags(owners):
    """Object-integrated tag tabs do not necessarily select the tag itself."""
    found = []
    seen = {node for node in owners if isinstance(node, c4d.BaseTag)}
    scanned = 0
    for owner in owners:
        if not isinstance(owner, c4d.BaseObject):
            continue
        try:
            tag = owner.GetFirstTag()
            while tag is not None and scanned < MAX_ATTACHED_TAGS:
                scanned += 1
                if tag not in seen:
                    seen.add(tag)
                    found.append(tag)
                tag = tag.GetNext()
        except Exception:
            continue
        if scanned >= MAX_ATTACHED_TAGS:
            break
    return found


def _xpresso_nodes(owners):
    """Nodes selected in the XPresso editors of the selected objects."""
    found = []
    for owner in owners:
        tags = []
        if isinstance(owner, c4d.BaseObject):
            tags = [t for t in owner.GetTags() if t.CheckType(c4d.Texpresso)]
        elif isinstance(owner, c4d.BaseTag) and owner.CheckType(c4d.Texpresso):
            tags = [owner]
        for tag in tags:
            try:
                root = tag.GetNodeMaster().GetRoot()
            except Exception:
                continue
            found.extend(n for n in _walk(root.GetDown()) if n.GetBit(c4d.BIT_ACTIVE))
    return found[:MAX_SHADERS]


class _ToolNode(object):
    """The active tool's settings, as a node. They live in the document's
    tool container; the tool plugin itself only supplies the description."""

    def __init__(self, doc, plugin):
        self.doc, self.plugin, self.id = doc, plugin, plugin.GetID()

    def GetDataInstance(self):
        return c4d.plugins.GetToolData(self.doc, self.id)

    def GetDescription(self, flags):
        return self.plugin.GetDescription(flags)

    def GetParameter(self, descid, flags):
        v = self.GetDataInstance()[descid[0].id]
        if descid.GetDepth() == 2 and isinstance(v, c4d.Vector):
            return {c4d.VECTOR_X: v.x, c4d.VECTOR_Y: v.y, c4d.VECTOR_Z: v.z}[descid[1].id]
        return v

    def SetParameter(self, descid, value, flags):
        bc = self.GetDataInstance()
        pid = descid[0].id
        if descid.GetDepth() == 2:
            v = bc.GetVector(pid)
            if descid[1].id == c4d.VECTOR_X:
                v.x = value
            elif descid[1].id == c4d.VECTOR_Y:
                v.y = value
            else:
                v.z = value
            value = v
        bc[pid] = value
        return True

    def IsAlive(self):
        return True

    def GetName(self):
        return self.plugin.GetName()

    def GetDocument(self):
        return self.doc


class _UnitScale(object):
    """Project Scale: a number and a unit in one UnitScaleData."""

    def __init__(self, doc):
        self.doc = doc

    def vl_snapshot(self):
        try:
            return {c4d.DOCUMENT_DOCUNIT: ("r", self._get()[0])}
        except Exception:
            return {}

    def _get(self):
        return self.doc[c4d.DOCUMENT_DOCUNIT].GetUnitScale()

    def vl_info(self, key):
        bc = c4d.BaseContainer()
        bc[c4d.DESC_NAME] = "Project Scale"
        bc[c4d.DESC_MIN] = 1e-6
        bc[c4d.DESC_STEP] = 1.0
        return bc

    def GetParameter(self, descid, flags):
        return self._get()[0]

    def SetParameter(self, descid, value, flags):
        data = self.doc[c4d.DOCUMENT_DOCUNIT]
        data.SetUnitScale(value, data.GetUnitScale()[1])
        return self.doc.SetParameter(c4d.DescID(c4d.DOCUMENT_DOCUNIT), data, flags)

    def IsAlive(self):
        return self.doc.IsAlive()

    def GetName(self):
        return "Project Scale"

    def GetDocument(self):
        return self.doc


class _FieldLayer(object):
    """A layer of a field list. The list is a copy whenever it is read, so
    every change goes through a fresh copy written back to its owner."""

    def __init__(self, owner, list_id, index):
        self.owner, self.list_id, self.index = owner, list_id, index
        self.list_descid = c4d.DescID(c4d.DescLevel(list_id, c4d.CUSTOMDATATYPE_FIELDLIST, 0))

    def _layer(self, fields=None):
        if fields is None:
            fields = self.owner.GetParameter(self.list_descid, c4d.DESCFLAGS_GET_NONE)
        for i, layer in enumerate(_walk(fields.GetLayersRoot().GetDown())):
            if i == self.index:
                return fields, layer
        raise LookupError("field layer gone")

    # The layer dies with its list: keep both alive while it is used.
    def vl_snapshot(self):
        try:
            fields, layer = self._layer()
            return _snapshot(layer)
        except Exception:
            return {}

    def vl_info(self, key):
        fields, layer = self._layer()
        desc = layer.GetDescription(c4d.DESCFLAGS_DESC_NONE)
        bc = desc.GetParameterI(c4d.DescID(c4d.DescLevel(key)), None)
        return c4d.BaseContainer(bc) if bc is not None else None

    def GetParameter(self, descid, flags):
        fields, layer = self._layer()
        return layer.GetParameter(descid, flags)

    def SetParameter(self, descid, value, flags):
        fields, layer = self._layer()
        layer.SetParameter(descid, value, flags)
        return self.owner.SetParameter(self.list_descid, fields, flags)

    def IsAlive(self):
        return self.owner.IsAlive()

    def GetName(self):
        return self.owner.GetName()

    def GetDocument(self):
        return self.owner.GetDocument()


def _field_layers(owners, doc):
    """Layers of the field lists on owners, plus the field objects they use."""
    found = []
    for owner in owners[:MAX_FIELD_OWNERS]:
        try:
            desc = owner.GetDescription(c4d.DESCFLAGS_DESC_NONE)
            ids = [descid[0].id for _, descid, _ in desc
                   if descid.GetDepth() == 1 and descid[0].dtype == c4d.CUSTOMDATATYPE_FIELDLIST]
        except Exception:
            continue
        for list_id in ids:
            try:
                fields = owner[list_id]
                layers = list(_walk(fields.GetLayersRoot().GetDown()))
            except Exception:
                continue
            for i, layer in enumerate(layers[:MAX_SHADERS]):
                found.append(_FieldLayer(owner, list_id, i))
                try:
                    linked = layer.GetLinkedObject(doc)
                    if linked is not None:
                        found.append(linked)
                except Exception:
                    pass
    return found


class _NodePorts(object):
    """Editable numeric constants in an existing node-material graph.

    Port keys include the graph identity. Read/write operations preserve the
    SDK datatype and never replace connected inputs or read-only graphs.
    """

    def __init__(self, material, graph, node):
        self.material, self.graph = material, graph
        self.path = node.GetPath()
        self.types, self.paths = {}, {}
        self.prefix = "material:%s:" % graph

    def IsAlive(self):
        return self.material.IsAlive()

    def GetName(self):
        return self.material.GetName()

    def GetDocument(self):
        return self.material.GetDocument()

    _defaults = None

    @classmethod
    def _value(cls, port):
        value = port.GetPortValue()
        if value is not None:
            return value
        # An unconnected numeric input can be unset while its UI displays
        # the datatype's default (Arithmetic inputs, for example). Snapshot
        # that default without writing it into the graph. This is only a
        # fallback for empty, unconnected inputs, never a running wire type.
        fixed = port.GetValue("fixedtype")
        if fixed is None:
            return None
        if cls._defaults is None:
            names = ("Float32", "Float64", "Int16", "Int32", "Int64",
                     "UInt16", "UInt32", "UInt64", "Vector32", "Vector64")
            cls._defaults = [(getattr(maxon, name)().GetType(), getattr(maxon, name))
                             for name in names]
        for dtype, typ in cls._defaults:
            if fixed == dtype:
                return typ()
        return None

    @staticmethod
    def _kind(value):
        name = type(value).__name__
        if name in ("Vector", "Vector32", "Vector64"):
            return "v"
        if name.startswith("Float") or type(value) is float:
            return "r"
        if name.startswith(("Int", "UInt")) or type(value) is int:
            return "l"
        return None   # enums, booleans, matrices, arrays and geometry

    @staticmethod
    def _connected(port):
        # Failure to inspect a port must never turn into permission to
        # overwrite a connected value. Connections are left untouched.
        try:
            return bool(list(port.GetConnections(maxon.PORT_DIR.INPUT)))
        except Exception:
            return True

    @classmethod
    def _connected_component(cls, port):
        stack = list(port.GetChildren())
        visited = 0
        while stack and visited < MAX_SCENE_PORTS:
            child = stack.pop()
            visited += 1
            if cls._connected(child):
                return True
            stack.extend(child.GetChildren())
        return bool(stack)  # an uninspected component is not safe to write

    def vl_snapshot(self):
        snap = {}
        try:
            if self.graph.IsReadOnly():
                return snap
            node = self.graph.GetNode(self.path)
            stack = list(reversed(node.GetInputs().GetChildren()))
        except Exception:
            return snap
        visited = 0
        while stack and visited < MAX_SCENE_PORTS:
            port = stack.pop()
            visited += 1
            try:
                if self._connected(port):
                    continue
                value = self._value(port)
                kind = self._kind(value)
                fixed = port.GetValue("fixedtype")
                if fixed is not None and str(fixed).startswith("enum<"):
                    continue
                if kind is not None:
                    if kind == "v" and self._connected_component(port):
                        continue
                    key = self.prefix + str(port.GetPath())
                    self.paths[key] = port.GetPath()
                    self.types[key] = type(value)
                    if kind == "v":
                        snap[key] = (kind, (float(value.x), float(value.y), float(value.z)))
                    else:
                        snap[key] = (kind, float(value) if kind == "r" else int(value))
                    continue   # do not also snapshot synthetic XYZ ports
                stack.extend(reversed(port.GetChildren()))
            except Exception:
                continue
        return snap

    def vl_info(self, key):
        port = self.graph.GetNode(self.paths[key])
        bc = c4d.BaseContainer()
        bc[c4d.DESC_NAME] = str(port.GetValue(maxon.NODE.BASE.NAME) or port.GetId())
        for attr, desc in ((maxon.DESCRIPTION.DATA.BASE.MINVALUE, c4d.DESC_MIN),
                           (maxon.DESCRIPTION.DATA.BASE.MAXVALUE, c4d.DESC_MAX)):
            value = port.GetValue(attr)
            kind = self._kind(value)
            if kind == "v":
                bc[desc] = c4d.Vector(value.x, value.y, value.z)
            elif kind is not None:
                bc[desc] = float(value)
        return bc

    def GetParameter(self, descid, flags):
        value = self._value(self.graph.GetNode(self.paths[descid[0]]))
        if descid[1] is not None:
            return float((value.x, value.y, value.z)[descid[1]])
        return float(value) if self._kind(value) == "r" else int(value)

    def SetParameter(self, descid, value, flags):
        if value is None or self.graph.IsReadOnly():
            return False
        key, component = descid
        port = self.graph.GetNode(self.paths[key])
        if self._connected(port):
            return False
        current = self._value(port)
        typ = self.types[key]
        if type(current) is not typ:
            return False   # the node's datatype changed during the gesture
        if component is not None:
            if self._connected_component(port):
                return False
            parts = [current.x, current.y, current.z]
            parts[component] = value
            data = typ(*parts)
        else:
            data = typ(value)
        undo = maxon.DataDictionary()
        undo.Set(maxon.nodes.UndoMode, maxon.nodes.UNDO_MODE.NONE)
        with self.graph.BeginTransaction(undo) as transaction:
            port.SetPortValue(data)
            transaction.Commit()
        return True


class _SceneNodePorts(_NodePorts):
    """The same numeric adapter with Scene Nodes owner undo tracking."""

    def __init__(self, owner, graph, node):
        super().__init__(owner, graph, node)
        self.prefix = "scene:%s:" % graph
        self.undo_before = self._undo_marker()

    def _undo_marker(self):
        # Scene-hook undo entries have no GetUndoPtr(), even when the
        # graph change is undoable. Find the owner's saved state instead.
        state = self.GetDocument().FindUndoPtr(self.material, c4d.UNDOTYPE_CHANGE)
        return state is not None

    def vl_has_scrub_undo(self):
        marker = self._undo_marker()
        return marker and not self.undo_before


def _selected_graph_nodes(graph):
    """Include nested group selections without opening an editor view."""
    selected = []
    # The callback overload also works with Redshift graphs, whose list
    # overload can raise "expected generic datatype capsule" in C4D 2027.
    maxon.GraphModelHelper.GetSelectedNodes(
        graph, maxon.NODE_KIND.NODE, lambda n: selected.append(n) or True)
    paths = {str(n.GetPath()) for n in selected}
    stack = list(reversed(graph.GetViewRoot().GetChildren(mask=maxon.NODE_KIND.NODE)))
    visited = 0
    while stack and visited < MAX_SCENE_SELECTION_NODES:
        node = stack.pop()
        visited += 1
        path = str(node.GetPath())
        if path not in paths and maxon.GraphModelHelper.IsNodeSelected(node):
            selected.append(node)
            paths.add(path)
        stack.extend(reversed(node.GetChildren(mask=maxon.NODE_KIND.NODE)))
    return selected


def _node_ports(materials, doc=None):
    """Selected materials, plus existing graphs with selected nodes.

    Clicking a node does not necessarily select its material in the Material
    Manager. Check other materials within a budget; do not create graphs or
    scan every unselected graph's input ports.
    """
    found = []
    active = list(materials)[:MAX_MATERIALS]
    sources = list(active)
    if doc is not None:
        for mat in doc.GetMaterials():
            if len(sources) >= MAX_MATERIALS:
                break
            if mat not in sources:
                sources.append(mat)
    try:
        space = c4d.GetActiveNodeSpaceId()
    except Exception:
        return found
    deadline = time.perf_counter() + MATERIAL_SCAN_BUDGET
    for mat in sources:
        if mat not in active and time.perf_counter() > deadline:
            break
        try:
            ref = mat.GetNodeMaterialReference()
            if not ref.HasSpace(space):
                continue
            graph = ref.GetGraph(space)
            if graph.IsNullValue() or graph.IsReadOnly():
                continue
            selected = _selected_graph_nodes(graph)
            if not selected and mat not in active:
                continue
            nodes = selected or graph.GetViewRoot().GetChildren(mask=maxon.NODE_KIND.NODE)
            found.extend(_NodePorts(mat, graph, n) for n in nodes[:MAX_GRAPH_NODES])
        except Exception:
            continue
    return found


def _scene_node_ports(doc, owners):
    """Read existing nimbus refs, without creating a graph on ordinary nodes.

    The document-wide graph belongs to the Scene Nodes scene hook. Capsule
    graphs belong to their selected objects/tags. No GraphDescription.GetGraph
    here: that convenience function would create missing graphs.
    """
    sources = list(owners)
    try:
        hook = doc.FindSceneHook(c4d.SCENENODES_IDS_SCENEHOOK_ID)
        if hook is not None:
            sources.insert(0, hook)
    except Exception:
        pass
    found, seen = [], set()
    for owner in sources:
        try:
            nimbus = owner.GetNimbusRef(maxon.NodeSpaceIdentifiers.SceneNodes)
            if nimbus is None:
                continue
            graph = nimbus.GetGraph()
            identity = str(graph)
            if graph.IsNullValue() or graph.IsReadOnly() or identity in seen:
                continue
            seen.add(identity)
            selected = _selected_graph_nodes(graph)
            # Selected nodes may be inside groups. Without a selection,
            # only scan the visible root's children: descending into every
            # asset would also inspect its hidden implementation nodes.
            nodes = selected or graph.GetViewRoot().GetChildren(mask=maxon.NODE_KIND.NODE)
            found.extend(_SceneNodePorts(owner, graph, n) for n in nodes[:MAX_GRAPH_NODES])
        except Exception:
            continue
    return found


def _selected_keys(doc, owners):
    """Keys selected in a Timeline, the selected objects' first, within a
    time budget: scenes can hold millions of keys."""
    found = []
    seen = set()
    deadline = time.perf_counter() + KEY_SCAN_BUDGET
    bits = (c4d.NBIT_TL1_SELECT, c4d.NBIT_TL2_SELECT, c4d.NBIT_TL3_SELECT, c4d.NBIT_TL4_SELECT)

    def scan(node):
        for track in node.GetCTracks():
            curve = track.GetCurve()
            for i in range(curve.GetKeyCount()):
                key = curve.GetKey(i)
                if any(key.GetNBit(b) for b in bits):
                    found.append(key)

    def everything():
        for obj in _walk(doc.GetFirstObject()):
            yield obj
            for tag in obj.GetTags():
                yield tag
        for mat in doc.GetMaterials():
            yield mat

    for node in list(owners) + [None]:
        for n in ([node] if node is not None else everything()):
            if time.perf_counter() > deadline or len(found) >= MAX_SHADERS:
                return found
            try:
                h = hash(n)
                if h in seen:
                    continue
                seen.add(h)
                scan(n)
            except Exception:
                pass
    return found


_prefs_descids = None   # [(plugin, [(key, descid)])], built on first use


class _PrefsPage(object):
    """A page of the Preferences window."""

    def __init__(self, plugin, entries):
        self.plugin, self.entries = plugin, entries

    def vl_snapshot(self):
        snap = {}
        for key, descid, kind in self.entries:
            try:
                v = self.plugin.GetParameter(descid, c4d.DESCFLAGS_GET_NONE)
                if kind == "v":
                    snap[key] = ("v", (v.x, v.y, v.z))
                elif kind == "r":
                    snap[key] = ("r", float(v))
                elif kind == "l":
                    snap[key] = ("l", int(v))
            except Exception:
                pass
        return snap

    def GetDescription(self, flags):
        return self.plugin.GetDescription(flags)

    def GetParameter(self, descid, flags):
        return self.plugin.GetParameter(descid, flags)

    def SetParameter(self, descid, value, flags):
        return self.plugin.SetParameter(descid, value, flags)

    def IsAlive(self):
        return True

    def GetName(self):
        return self.plugin.GetName()

    def GetDocument(self):
        return c4d.documents.GetActiveDocument()


def _prefs_pages():
    """Every preferences page. Walking their descriptions is slow (a third
    of a second), so it happens once and only the values are read after."""
    global _prefs_descids
    if _prefs_descids is None:
        _prefs_descids = []
        for plugin in c4d.plugins.FilterPluginList(c4d.PLUGINTYPE_PREFS, True):
            entries = []
            try:
                for _, descid, _ in plugin.GetDescription(c4d.DESCFLAGS_DESC_NONE):
                    if descid.GetDepth() != 1:
                        continue
                    kind = _DESC_KINDS.get(descid[0].dtype)
                    if kind in ("r", "l", "v"):
                        entries.append((descid[0].id, descid, kind))
            except Exception:
                pass
            if entries:
                _prefs_descids.append((plugin, entries))
    return [_PrefsPage(p, e) for p, e in _prefs_descids]


def _candidates(doc, floating=False, node_editor=False):
    """Nodes whose parameters the Attribute Manager may be showing, the
    likeliest first: ties in the diff go to the earlier node."""
    nodes = []
    for get in (lambda: doc.GetActiveObjects(c4d.GETACTIVEOBJECTFLAGS_NONE),
                doc.GetActiveTags, doc.GetActiveMaterials):
        try:
            nodes.extend(get())
        except Exception:
            pass
    nodes = nodes[:MAX_CANDIDATES]
    primary = list(nodes)
    materials = [n for n in primary if isinstance(n, c4d.BaseMaterial)]
    if node_editor:
        # The Node Editor's own inspector cannot be showing project, tool
        # or Preferences settings. Avoid their expensive description scans.
        return _node_ports(materials, doc) + _scene_node_ports(doc, primary)
    attached = _attached_tags(primary)
    nodes.extend(attached)
    nodes.extend(_shaders(primary + attached))
    nodes.extend(_xpresso_nodes(primary))
    nodes.extend(_field_layers(primary + nodes[len(primary):], doc))
    nodes.extend(_node_ports(materials, doc))
    nodes.extend(_scene_node_ports(doc, primary))
    nodes.extend(_selected_keys(doc, primary))
    # Selected layers.
    try:
        root = doc.GetLayerObjectRoot()
        nodes.extend(l for l in _walk(root.GetDown()) if l.GetBit(c4d.BIT_ACTIVE))
    except Exception:
        pass
    nodes.append(doc)
    nodes.append(_UnitScale(doc))
    # Settings pages: render settings and their effects, the active tool,
    # the viewport, the current take, then the scene hooks (the Scene tabs
    # such as Bullet, Simulation, XRefs).
    rd = doc.GetActiveRenderData()
    if rd is not None:
        nodes.append(rd)
        try:
            nodes.extend(_walk(rd.GetFirstVideoPost()))
        except Exception:
            pass
    try:
        tool = c4d.plugins.FindPlugin(doc.GetAction(), c4d.PLUGINTYPE_TOOL)
        if tool is not None:
            nodes.append(_ToolNode(doc, tool))
    except Exception:
        pass
    for get in (doc.GetActiveBaseDraw,
                lambda: doc.GetTakeData().GetCurrentTake()):
        try:
            n = get()
            if n is not None:
                nodes.append(n)
        except Exception:
            pass
    try:
        for branch in doc.GetBranchInfo():
            if branch["id"] == c4d.SHplugin:   # the scene hooks
                nodes.extend(_walk(branch["head"].GetFirst()))
    except Exception:
        pass
    if floating:
        # Pressed in a window of its own, such as the Preferences.
        try:
            nodes.extend(_prefs_pages())
        except Exception:
            pass
    return nodes


def _descid(key, kind, comp=None):
    if isinstance(key, str):
        return (key, comp)   # an adapter's own key, see _NodePorts
    ids = key if isinstance(key, tuple) else (key,)
    levels = [c4d.DescLevel(i, c4d.DTYPE_SUBCONTAINER, 0) for i in ids[:-1]]
    levels.append(c4d.DescLevel(ids[-1], _KIND_DTYPES[kind], 0))
    if comp is not None:
        levels.append(c4d.DescLevel(_VCOMP[comp], c4d.DTYPE_REAL, 0))
    return c4d.DescID(*levels)


class Param(object):
    """The parameter being scrubbed, plus its display conversion and limits."""

    def __init__(self, node, pid, kind, comp, snapshot=None):
        self.pid, self.kind, self.comp = pid, kind, comp
        self.descid = _descid(pid, kind, comp if kind == "v" else None)
        self.is_int = kind in ("l", "t")   # times are scrubbed in whole frames
        self.fps = _fps(node)
        self.project_ranges = None
        self.rs_optics = (snapshot if snapshot is not None and _is_rs_camera(node)
                          and pid in _rs_optics_ids()
                          and hasattr(snapshot, "optics") else None)
        self.rs_optics_by_node = {node: self.rs_optics} if self.rs_optics is not None else {}
        self.rs_focus_by_node = ({node: snapshot.focus_distance}
                                if _is_rs_camera(node) and pid == c4d.RSCAMERAOBJECT_FOCUS_OFFSET
                                and snapshot is not None and hasattr(snapshot, "focus_distance") else {})
        self.original_time = snapshot.get(pid, (None, None))[1] if snapshot is not None and kind == "t" else None
        if (snapshot is not None and isinstance(node, c4d.documents.BaseDocument)
                and pid in (c4d.DOCUMENT_MINTIME, c4d.DOCUMENT_MAXTIME)
                and all(key in snapshot for key in _PROJECT_RANGES)):
            self.project_ranges = {key: snapshot[key][1] for key in _PROJECT_RANGES}
        self.name = str(pid)
        self.unit = None
        self.vmin = self.vmax = None
        self.step = 1.0
        bc = None
        try:
            if hasattr(node, "vl_info"):
                bc = node.vl_info(pid)
            else:
                desc = node.GetDescription(c4d.DESCFLAGS_DESC_NONE)
                bc = desc.GetParameterI(_descid(pid, kind), None)
        except Exception:
            pass
        if bc is not None:
            self.name = bc[c4d.DESC_NAME] or self.name
            self.unit = bc[c4d.DESC_UNIT]
            self.vmin = self._component(bc[c4d.DESC_MIN])
            self.vmax = self._component(bc[c4d.DESC_MAX])
            step = self._component(bc[c4d.DESC_STEP])
            if step:
                self.step = abs(step)
        if kind == "v":
            self.name += " . " + "XYZ"[comp]
        # display = internal * scale
        if self.unit == c4d.DESC_UNIT_DEGREE:
            self.scale, self.suffix = 180.0 / math.pi, u"°"
        elif self.unit == c4d.DESC_UNIT_PERCENT:
            self.scale, self.suffix = 100.0, "%"
        elif kind == "t":
            self.scale, self.suffix = 1.0, " F"
            self.step = 1.0   # the description's step is a second, not a frame
        else:
            self.scale, self.suffix = 1.0, ""

    def _component(self, v):
        if v is None:
            return None
        if isinstance(v, c4d.Vector):
            return (v.x, v.y, v.z)[self.comp if self.comp is not None else 0]
        if isinstance(v, c4d.BaseTime):
            return v.Get() * self.fps
        try:
            return float(v)
        except Exception:
            return None

    def start_rung(self, rungs):
        """Index of the rung closest to the parameter's own step size."""
        disp_step = self.step * self.scale
        best, best_d = 0, None
        for i, r in enumerate(rungs):
            d = abs(math.log10(r) - math.log10(max(disp_step, 1e-9)))
            if best_d is None or d < best_d:
                best, best_d = i, d
        return best

    def get(self, node):
        v = node.GetParameter(self.descid, c4d.DESCFLAGS_GET_NONE)
        if self.kind == "t":
            return v.Get() * self.fps
        return v

    def set(self, node, value):
        if self.vmin is not None:
            value = max(self.vmin, value)
        if self.vmax is not None:
            value = min(self.vmax, value)
        if self.is_int:
            value = int(round(value))
        data = c4d.BaseTime(value, self.fps) if self.kind == "t" else value
        descid = self.descid
        flags = c4d.DESCFLAGS_SET_USERINTERACTION
        if _is_rs_camera(node) and self.pid == c4d.RSCAMERAOBJECT_FOV:
            # The composite FOV setter can compare against stale stored XY
            # values. Drive the focal length from the current optical ratio.
            current = self.get(node)
            lens_id = _descid(c4d.RSCAMERAOBJECT_FOCAL_LENGTH, "r")
            focal = node.GetParameter(lens_id, c4d.DESCFLAGS_GET_NONE)
            data = focal * math.tan(current / 2.0) / math.tan(value / 2.0)
            descid = lens_id
        elif self.kind == "v" and _is_rs_camera(node):
            # RS Camera's two-dimensional gadgets consume a complete vector;
            # a scalar component write can zero the other sensor dimension.
            descid = _descid(self.pid, self.kind)
            data = c4d.Vector(node.GetParameter(descid, c4d.DESCFLAGS_GET_NONE))
            data[self.comp] = value
            # Limits above apply to the edited component. In a two-value
            # gadget, C4D must not clamp the unused Z padding to its XY min.
            flags |= c4d.DESCFLAGS_SET_DONTCHECKMINMAX
        node.SetParameter(descid, data, flags)
        return value

    def from_snapshot(self, value):
        return value.Get() * self.fps if self.kind == "t" else value

    def restore(self, node, value):
        if node in self.rs_focus_by_node:
            self.set(node, value)
            # Native AM updates can also write the disabled Focus Distance
            # cache. It is an output while focus is linked, not a second input.
            # Restore only the known numeric cache; never send description
            # update messages into Redshift from Python.
            data = node.GetDataInstance()
            pid = c4d.RSCAMERAOBJECT_FOCUS_DISTANCE
            if _rs_linked_focus(node) and data.GetType(pid) == c4d.DA_REAL:
                data.SetFloat(pid, self.rs_focus_by_node[node])
            return value
        if self.rs_optics is not None:
            _rs_restore_optics(node, self.rs_optics_by_node.get(node, self.rs_optics))
            return value
        if self.project_ranges is not None:
            # Expanding a range again does not put its clamped preview
            # endpoints back. Restore all four exact times from the baseline.
            setters = (node.SetMinTime, node.SetMaxTime, node.SetLoopMinTime, node.SetLoopMaxTime)
            for key, setter in zip(_PROJECT_RANGES, setters):
                setter(self.project_ranges[key])
            return value
        if self.kind == "t":
            # Baselines can contain subframes even though ladder steps are
            # whole frames. Restoration must not round the original time.
            exact = self.original_time
            if exact is None:
                exact = c4d.BaseTime()
                exact.SetNumerator(value)
                exact.SetDenominator(self.fps)
            node.SetParameter(self.descid, exact, c4d.DESCFLAGS_SET_USERINTERACTION)
            return value
        return self.set(node, value)

    def fmt(self, internal):
        v = internal * self.scale
        if self.is_int:
            return "%d" % int(round(v)) + self.suffix
        return ("%.4f" % v).rstrip("0").rstrip(".") + self.suffix


# ---------------------------------------------------------------------------
# Overlay
# ---------------------------------------------------------------------------
class LadderOverlay(object):
    """A native bitmap popup, born borderless and hidden.

    Completed off-screen frames are composed with UpdateLayeredWindow.
    The STATIC class is only a window shell: no control bitmap replacement,
    background erase, C4D dialog initialization or Python window procedure.
    """

    def __init__(self, ctrl):
        self.ctrl = ctrl
        self.native_hwnd = None
        self.bitmap = None
        self.font = None
        self.rect = None
        self.frame_key = None

    @staticmethod
    def _rgb(r, g, b):
        return r | (g << 8) | (b << 16)

    def Open(self, left, top, width, height):
        self.rect = (left, top, width, height)
        ex = WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED | WS_EX_TRANSPARENT
        # No WS_VISIBLE, caption or sizing frame: nothing can flash before
        # the first finished bitmap is installed and Show() is called.
        self.native_hwnd = user32.CreateWindowExW(
            ex, "STATIC", None, WS_POPUP, left, top, width, height,
            None, None, None, None)
        if not self.native_hwnd:
            raise RuntimeError("could not create native ladder window")
        try:
            self.font = gdi32.CreateFontW(-12, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI")
            if not self.font:
                raise RuntimeError("could not create ladder font")
            self.refresh()
        except Exception:
            self.Close()
            raise
        return True

    def Show(self):
        if not self.native_hwnd or not self.bitmap:
            return False
        left, top, width, height = self.rect
        return bool(user32.SetWindowPos(self.native_hwnd, HWND_TOPMOST, left, top, width, height,
                                        SWP_SHOWWINDOW | SWP_NOACTIVATE))

    def _draw(self, dc, width, height, frame):
        def fill(rect, color):
            brush = gdi32.CreateSolidBrush(self._rgb(*color))
            if not brush:
                raise RuntimeError("could not allocate ladder brush")
            try:
                user32.FillRect(dc, ctypes.byref(wt.RECT(*rect)), brush)
            finally:
                gdi32.DeleteObject(brush)

        def label(text, rect, color):
            gdi32.SetTextColor(dc, self._rgb(*color))
            user32.DrawTextW(dc, str(text), -1, ctypes.byref(wt.RECT(*rect)),
                            0x0001 | 0x0004 | 0x0020 | 0x0800)  # center, vcenter, singleline, noprefix

        fill((0, 0, width, height), (33, 33, 33))
        rungs, active_rung, last_rung, readout = frame
        if not rungs:
            return
        rh = height // (len(rungs) + 1)
        gdi32.SetBkMode(dc, 1)  # transparent text background
        for index, value in enumerate(rungs):
            y = index * rh
            active = index == active_rung
            usable = index <= last_rung
            color = (77, 87, 158) if active else (51, 51, 51) if usable else (38, 38, 38)
            fill((2, y + 1, width - 2, y + rh - 1), color)
            fg = (255, 255, 255) if active else (191, 191, 191) if usable else (102, 102, 102)
            label('%g' % value, (0, y, width, y + rh), fg)
        y = len(rungs) * rh
        fill((2, y + 1, width - 2, height - 1), (20, 20, 20))
        label(readout, (0, y, width, height), (255, 217, 102))

    def refresh(self):
        if not self.native_hwnd:
            return
        frame = (tuple(self.ctrl.rungs or []), self.ctrl.rung, self.ctrl.last_rung, self.ctrl.readout())
        if frame == self.frame_key:
            return
        left, top, width, height = self.rect
        screen = user32.GetDC(None)
        if not screen:
            raise RuntimeError("could not get ladder drawing context")
        dc = bitmap = old_bitmap = old_font = None
        complete = False
        try:
            dc = gdi32.CreateCompatibleDC(screen)
            bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
            if not dc or not bitmap:
                raise RuntimeError("could not allocate ladder bitmap")
            old_bitmap = gdi32.SelectObject(dc, bitmap)
            old_font = gdi32.SelectObject(dc, self.font)
            self._draw(dc, width, height, frame)
            # Submit a complete opaque frame directly to the compositor.
            # Do not use STM_SETIMAGE / WM_PAINT: ordinary STATIC painting
            # can expose its white background between image replacements.
            destination, size, source = wt.POINT(left, top), wt.SIZE(width, height), wt.POINT(0, 0)
            if not user32.UpdateLayeredWindow(self.native_hwnd, screen, ctypes.byref(destination),
                    ctypes.byref(size), dc, ctypes.byref(source), 0, None, ULW_OPAQUE):
                raise RuntimeError("could not present ladder frame: %s" % ctypes.get_last_error())
            complete = True
        finally:
            if dc:
                if old_font:
                    gdi32.SelectObject(dc, old_font)
                if old_bitmap:
                    gdi32.SelectObject(dc, old_bitmap)
                gdi32.DeleteDC(dc)
            user32.ReleaseDC(None, screen)
            if bitmap and not complete:
                gdi32.DeleteObject(bitmap)
        old_source, self.bitmap = self.bitmap, bitmap
        self.frame_key = frame
        if old_source:
            gdi32.DeleteObject(old_source)

    def Close(self):
        if self.native_hwnd:
            user32.DestroyWindow(self.native_hwnd)
            self.native_hwnd = None
        for resource in set((self.bitmap, self.font)) - {None, 0}:
            gdi32.DeleteObject(resource)
        self.bitmap = self.font = self.frame_key = None


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------
IDLE, PENDING, IDENTIFYING, LADDER, PREPARING = 0, 1, 2, 3, 4


class Controller(object):
    """Drives one gesture: press, identify, ladder, release.

    Threads: the hook thread only records input and wakes the main thread;
    all Cinema 4D calls happen in pump(), on the main thread.
    """

    def __init__(self, wake):
        self.wake = wake              # callable that wakes the main thread
        self.enabled = True
        self.lock = threading.Lock()
        self.events = deque()
        self.capturing = False        # a gesture is running; buttons are swallowed
        self.frozen = False           # native scrub running; swallow pointer moves
        self.origin = (0, 0)
        self.pt = (0, 0)              # latest pointer position
        self.ladder_rect = None       # (left, top, right, bottom) once the ladder shows
        self.wrap_side = 0            # edge last wrapped at (1 right, -1 left) until back
        self.out_dx = 0               # sideways travel made outside the ladder
        self.held_dx = self.held_dy = 0  # user motion while the scrub owns the cursor
        self.resume_pt = (0, 0)
        self.cursor_hidden = False
        self.cursors_swapped = False
        self.released = False
        self.cancelled = False
        self.pending = False
        self.phase = IDLE
        self.detector = FieldDetector()
        self.pid = kernel32.GetCurrentProcessId()
        self.hook_thread = None
        self.hook_tid = 0
        self._hook_proc = HOOKPROC(self._proc)
        self._key_proc = HOOKPROC(self._proc_key)
        self.focus_probe = False
        self.focus_attempt = 0
        self.input_root = None
        self.probe_error = None
        self.probe_serial = 0
        self.undo_marks = []
        # ladder state
        self.param = None
        self.targets = []
        self.originals = []
        self.rungs = RUNGS
        self.rung = RUNGS.index(1.0)
        self.last_rung = len(RUNGS) - 1
        self.offset = 0.0
        self.base_dx = 0
        self.steps = 0
        self.overlay = None
        self.overlay_rect = None
        self.last_event = 0.0
        self.last_draw = 0.0
        self.log = deque(maxlen=200)    # debug trail, read it from the console

    def _dbg(self, *parts):
        line = "%.3f [%s] %s" % (time.time() % 1000, threading.current_thread().name,
                                 " ".join(str(p) for p in parts))
        self.log.append(line)
        if DEBUG_LOG:
            try:
                with open(DEBUG_LOG, "a") as f:
                    f.write(line + "\n")
            except Exception:
                pass

    # -- hook thread ---------------------------------------------------------
    def start(self):
        if self.hook_thread is not None:
            return
        self.hook_thread = threading.Thread(target=self._hook_loop, name="ValueLadderHook")
        self.hook_thread.daemon = True
        self.hook_thread.start()

    def stop(self):
        if self.hook_tid:
            user32.PostThreadMessageW(self.hook_tid, WM_QUIT, 0, 0)
        if self.hook_thread is not None:
            self.hook_thread.join(1.0)
        self.hook_thread = None
        self.hook_tid = 0
        self.probe_serial += 1
        self._close_overlay()
        self._release_capture()
        if self.cursors_swapped:
            _cursors_scrub(False)
            self.cursors_swapped = False

    def _hook_loop(self):
        self.hook_tid = kernel32.GetCurrentThreadId()
        hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._hook_proc, None, 0)
        if not hook:
            print("[ValueLadder] SetWindowsHookEx failed: %d" % ctypes.get_last_error())
            return
        key_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._key_proc, None, 0)
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        user32.UnhookWindowsHookEx(hook)
        if key_hook:
            user32.UnhookWindowsHookEx(key_hook)

    def _post(self, ev=None):
        with self.lock:
            if ev is not None:
                self.events.append(ev)
            if self.pending and ev is None:
                return
            self.pending = True
        self.wake()

    def _proc(self, code, wparam, lparam):
        try:
            if code == 0:
                ms = MSLLHOOKSTRUCT.from_address(lparam)
                if ms.dwExtraInfo != INJECT_MAGIC and self._on_mouse(wparam, ms):
                    return 1
        except Exception:
            pass
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _proc_key(self, code, wparam, lparam):
        try:
            if code == 0 and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                key = KBDLLHOOKSTRUCT.from_address(lparam)
                if key.dwExtraInfo != INJECT_MAGIC:
                    if self.capturing and self.focus_probe:
                        self.cancelled = True
                        self._post("cancel")
        except Exception:
            pass
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _on_mouse(self, msg, ms):
        """Return True to swallow the event."""
        if not self.capturing:
            if msg == WM_MBUTTONDOWN and self.enabled and self.phase == IDLE:
                x, y = ms.pt.x, ms.pt.y
                over = self._over_field(x, y)
                self._dbg("mmb down", x, y, "field" if over else "pass")
                if over:
                    self.focus_probe = True
                    self.input_root = user32.GetAncestor(user32.WindowFromPoint(wt.POINT(x, y)), 2)
                    with self.lock:
                        self.capturing = True
                        self.frozen = False
                        self.origin = self.pt = (x, y)
                        self.ladder_rect = None
                        self.out_dx = 0
                        self.wrap_side = 0
                        self.released = self.cancelled = False
                    self._post("start")
                    return True
            return False
        if msg == WM_MOUSEMOVE:
            if self.frozen:
                # The scrub owns the cursor for a moment. Keep the user's own
                # motion and hand it back afterwards, so the pointer ends up
                # where the hand went instead of snapping back.
                cx, cy = _cursor_pos()
                with self.lock:
                    self.held_dx += ms.pt.x - cx
                    self.held_dy += ms.pt.y - cy
                return True
            x, y = ms.pt.x, ms.pt.y
            warp = None
            with self.lock:
                r = self.ladder_rect
                if r is not None:
                    outside = not (r[0] <= x < r[2])
                    # Only travel made beside the ladder scrubs, unless the
                    # pointer wrapped and is on its way back across it.
                    if outside or self.wrap_side:
                        self.out_dx += x - self.pt[0]
                    if self.wrap_side and (x >= r[2] if self.wrap_side > 0 else x < r[0]):
                        self.wrap_side = 0   # back on the side it left from
                    if outside and self.phase == LADDER:
                        warp = self._edge_warp(x)
                if warp is not None:
                    x, side = warp
                    # Two wraps the opposite ways cancel out.
                    self.wrap_side = 0 if self.wrap_side else side
                self.pt = (x, y)
            if warp is not None:
                # At a screen edge the pointer comes back in from the other
                # edge, as in Houdini, and the drag carries on.
                user32.SetCursorPos(x, y)
                self._post()
                return True
            self._post()
            return False  # the pointer moves freely, as in Houdini
        if msg == WM_MBUTTONUP:
            with self.lock:
                self.released = True
            self._post("release")
            return True
        if msg in (WM_RBUTTONDOWN, WM_RBUTTONUP):
            if msg == WM_RBUTTONDOWN:
                with self.lock:
                    self.cancelled = True
                self._post("cancel")
            return True
        if msg in (WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEWHEEL):
            return True
        return False

    def _edge_warp(self, x):
        """(new x, edge side) when the pointer reached a screen edge, or None."""
        left = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        right = left + user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) - 1
        if x >= right - EDGE_PX:
            return left + EDGE_PX + WARP_IN_PX, 1
        if x <= left + EDGE_PX:
            return right - EDGE_PX - WARP_IN_PX, -1
        return None

    def _over_field(self, x, y):
        hwnd = user32.WindowFromPoint(wt.POINT(x, y))
        if not hwnd:
            return False
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != self.pid:
            return False
        name = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, name, 128)
        if name.value.upper().startswith("MAXON-DRAWPORT"):
            return False  # viewport: leave camera navigation alone
        try:
            return self.detector.check(x, y)[0] == "field"
        except Exception:
            return False

    # -- main thread ---------------------------------------------------------
    def pump(self):
        """Called on the main thread whenever the hook wakes us."""
        with self.lock:
            events = list(self.events)
            self.events.clear()
            self.pending = False
            pt, out_dx = self.pt, self.out_dx
            released, cancelled = self.released, self.cancelled
        for ev in events:
            if ev == "start":
                self._begin()
            elif ev == "prepared":
                if self.phase == PREPARING:
                    self._hide_cursor(False)
                    with self.lock:
                        self.frozen = False
                    if (self.probe_error or self.released or self.cancelled
                            or not self._input_ready()):
                        self._dbg("prepare cancelled", self.probe_error or "released or focus changed")
                        self._release_capture()
                    else:
                        self._begin_ready()
            elif ev == "scrubbed":
                self._finish_identify()
        if self.phase == PENDING:
            if cancelled or released:
                self._dbg("released before leaving the ladder")
                self._close_overlay()
                self._release_capture()
                return
            side = self._pick_rung(pt)
            if side:
                self._launch_scrub(side, pt)
            elif self.overlay is not None:
                self.overlay.refresh()
        elif self.phase == LADDER:
            if cancelled:
                self._cancel()
                return
            self._update(pt, out_dx)
            if released:
                self._finish()

    def _hide_cursor(self, hide):
        """Main thread only. Balanced: ShowCursor keeps a counter."""
        if hide and not self.cursor_hidden:
            user32.ShowCursor(False)
            self.cursor_hidden = True
        elif not hide and self.cursor_hidden:
            user32.ShowCursor(True)
            self.cursor_hidden = False

    def _release_capture(self):
        self._hide_cursor(False)
        if self.cursors_swapped:
            _cursors_scrub(False)
            self.cursors_swapped = False
        with self.lock:
            self.capturing = False
            self.frozen = False
            self.ladder_rect = None
        self.phase = IDLE
        self.probe_serial += 1

    def _input_ready(self):
        """Never inject into a different foreground window or document."""
        return (self.capturing and self.input_root
                and user32.GetForegroundWindow() == self.input_root
                and c4d.documents.GetActiveDocument() == self.doc
                and not any(user32.GetAsyncKeyState(k) & 0x8000
                            for k in (0x10, 0x11, 0x12, 0x5B, 0x5C)))

    def _begin(self):
        """Middle-button down: focus the field before recording its value."""
        self.doc = c4d.documents.GetActiveDocument()
        if self.released or self.cancelled:
            self._release_capture()
            return
        if self.focus_probe and self._input_ready():
            self.phase = PREPARING
            self.probe_error = None
            serial = self.probe_serial
            with self.lock:
                self.frozen = True
                self.held_dx = self.held_dy = 0
                pt = self.pt
            self._hide_cursor(True)

            def valid():
                return (serial == self.probe_serial and not self.cancelled and not self.released
                        and user32.GetForegroundWindow() == self.input_root
                        and not any(user32.GetAsyncKeyState(k) & 0x8000
                                    for k in (0x10, 0x11, 0x12, 0x5B, 0x5C)))

            def ready():
                try:
                    if not valid():
                        raise RuntimeError("focus changed before automatic field selection")
                    _move_to(*self.origin)
                    _send_mouse(MOUSEEVENTF_LEFTDOWN)
                    _send_mouse(MOUSEEVENTF_LEFTUP)
                    time.sleep(0.04)
                    if not valid():
                        raise RuntimeError("focus changed during automatic field selection")
                    # Commit pending text only after focusing the target. A
                    # Return sent to the previously focused manager could run
                    # an unrelated command. This commit precedes the baseline.
                    _send_key(VK_RETURN)
                except Exception as error:
                    self.probe_error = str(error)
                finally:
                    time.sleep(0.05)
                    if serial == self.probe_serial:
                        with self.lock:
                            target = (pt[0] + self.held_dx, pt[1] + self.held_dy)
                            self.out_dx += target[0] - pt[0]
                            self.pt = self.resume_pt = target
                            self.held_dx = self.held_dy = 0
                        _move_to(*target)
                        self._post("prepared")
            threading.Thread(target=ready, name="ValueLadderPrepare", daemon=True).start()
            return
        self.focus_probe = False
        if not ENABLE_LEGACY_PROBE:
            self._close_overlay()
            self._release_capture()
            return
        self._begin_ready()

    def _capture_probe_state(self):
        node_editor = self._window_title(self.origin).startswith("Node Editor")
        self.cands = _candidates(self.doc, self._floating(self.origin), node_editor=node_editor)
        self.before = [_snapshot(n) for n in self.cands]
        self.noisy = [set() for _ in self.cands]
        self.undo_before = self.doc.GetUndoPtr()
        self.undo_marks = []
        for node in self.cands:
            owner = getattr(node, "material", getattr(node, "owner", node))
            try:
                state = self.doc.FindUndoPtr(owner, c4d.UNDOTYPE_CHANGE)
                self.undo_marks.append((owner, state is not None))
            except Exception:
                pass

    def _begin_ready(self):
        started = time.perf_counter()
        self._capture_probe_state()
        self.focus_attempt = 0
        self.probe_error = None
        self.scrub_undo = False
        self.tries = 0
        self.param = None
        self.targets = []
        self.rung = RUNGS.index(1.0)
        self.last_rung = len(RUNGS) - 1
        self.offset = 0.0
        self.steps = 0
        self.base_dx = 0
        self.phase = PENDING
        self._dbg("pending at", self.origin, "candidates", len(self.cands),
                  "snapshot_ms", round((time.perf_counter() - started) * 1000, 2))
        self._open_overlay()
        if self.overlay_rect is None:
            self._release_capture()
            return
        _cursors_scrub(True)
        self.cursors_swapped = True
        if self.focus_probe:
            self._launch_focus_probe()

    def _window_title(self, pt):
        try:
            hwnd = user32.GetAncestor(user32.WindowFromPoint(wt.POINT(*pt)), 2)  # GA_ROOT
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            return title.value
        except Exception:
            return ""

    def _floating(self, pt):
        """Whether a floating window needs Preferences candidates too."""
        title = self._window_title(pt)
        return bool(title) and "Cinema 4D" not in title and not title.startswith("Node Editor")

    def _pick_rung(self, pt):
        """Over the ladder: select the rung under the pointer and return 0.
        Beside it: return -1 or 1 for the side the pointer is on."""
        left, top, w, h = self.overlay_rect
        x, y = pt
        if x < left:
            return -1
        if x >= left + w:
            return 1
        rung = max(0, min(self.last_rung, (y - top) // RUNG_PX))
        if rung != self.rung:
            # Bank what was scrubbed on the old rung and start fresh.
            self.offset += (self.steps or 0) * RUNGS[self.rung]
            self.steps = 0
            with self.lock:
                self.base_dx = self.out_dx
            self.rung = rung
        return 0

    def _launch_focus_probe(self):
        """Try small expressions, whole-unit expressions, then native keys."""
        if self.cancelled or self.released or not self._input_ready():
            self._close_overlay()
            self._release_capture()
            return
        self.phase = IDENTIFYING
        kind, amount = FOCUS_PROBES[self.focus_attempt]
        self.focus_attempt += 1
        self.probe_error = None
        serial = self.probe_serial
        with self.lock:
            self.frozen = True
            self.held_dx = self.held_dy = 0
            pt = self.pt
        self._hide_cursor(True)
        # Refresh the baseline's noise mask without accepting a moving value
        # as a user-selected parameter.
        self.noisy = [{(k, c) for k, _, c in _diff(before, _snapshot(node))}
                      for node, before in zip(self.cands, self.before)]

        def run():
            try:
                if serial != self.probe_serial or user32.GetForegroundWindow() != self.input_root:
                    raise RuntimeError("focus changed before numeric probe")
                _move_to(*self.origin)
                # Give the gadget time to re-enter text editing after the
                # overlay activation. No horizontal native drag is involved.
                _send_mouse(MOUSEEVENTF_LEFTDOWN)
                _send_mouse(MOUSEEVENTF_LEFTUP)
                time.sleep(0.04)
                if (serial != self.probe_serial or self.cancelled
                        or user32.GetForegroundWindow() != self.input_root
                        or any(user32.GetAsyncKeyState(k) & 0x8000 for k in (0x10, 0x11, 0x12))):
                    raise RuntimeError("focus changed during numeric probe")
                _send_key(VK_END, hold=0.03)
                time.sleep(0.02)
                _send_probe_step(kind, amount)
                _send_key(VK_RETURN)
            except Exception as error:
                self.probe_error = str(error)
            finally:
                time.sleep(0.05)  # let C4D process the commit before the diff
                if serial == self.probe_serial:
                    with self.lock:
                        target = (pt[0] + self.held_dx, pt[1] + self.held_dy)
                        self.out_dx += target[0] - pt[0]
                        self.pt = self.resume_pt = target
                        self.held_dx = self.held_dy = 0
                    _move_to(*target)
                    self._dbg("focus probe", kind, amount, self.probe_error or "committed")
                    self._post("scrubbed")
        threading.Thread(target=run, name="ValueLadderFocusProbe", daemon=True).start()

    def _has_probe_undo(self):
        # FindUndoPtr wrappers expose the same atom identity across saved
        # states; repr() addresses identify temporary Python wrappers. Neither
        # proves that a NEW undo was added. Only a missing -> present change
        # is safe. When an older entry already exists, restore by value on
        # cancel; an empty native undo is preferable to undoing the user's work.
        undo = self.doc.GetUndoPtr()
        if undo is not None and undo != self.undo_before:
            return True
        for owner, before in self.undo_marks:
            try:
                state = self.doc.FindUndoPtr(owner, c4d.UNDOTYPE_CHANGE)
                if state is not None and not before:
                    return True
            except Exception:
                pass
        return any(isinstance(n, _SceneNodePorts) and n.vl_has_scrub_undo()
                   for n in self.cands)

    def _abort_identify(self, hits, message):
        # DoUndo can replace nodes/graphs. Never write cached handles after it.
        if self.scrub_undo:
            self.doc.DoUndo(False)
        else:
            for (pid, kind, comp), found in hits.items():
                for node, before in found:
                    value = before[pid][1]
                    try:
                        param = Param(node, pid, kind, comp, snapshot=before)
                        original = value[comp] if kind == "v" else param.from_snapshot(value)
                        param.restore(node, original)
                    except Exception:
                        pass
        self._dbg("identify aborted", message)
        c4d.StatusSetText(message)
        self._close_overlay()
        self._redraw(force_event=True)
        self._release_capture()

    def _launch_scrub(self, direction, pt):
        """First time the pointer leaves the ladder: let the field under the
        press point scrub itself a few pixels, the same way the user is
        dragging; the diff names the parameter. The ladder lets clicks
        through, so the field gets them."""
        if not ENABLE_LEGACY_PROBE:
            self._dbg("legacy probe blocked")
            self._close_overlay()
            self._release_capture()
            return
        self.phase = IDENTIFYING
        self.scrub_dir = direction
        if self.tries == 0:
            # Values that moved on their own since the press (simulations,
            # hooks that tick) would pass for the scrubbed parameter.
            self.noisy = []
            for i, node in enumerate(self.cands):
                try:
                    after = _snapshot(node)
                except Exception:
                    after = {}
                self.noisy.append({(k, c) for k, _, c in _diff(self.before[i], after)})
        with self.lock:
            self.frozen = True
            self.held_dx = self.held_dy = 0
        # Hide the pointer while the scrub borrows it, so its trip to the
        # field and back isn't seen. Cursor visibility is per thread, and
        # the main thread owns every Cinema 4D window.
        self._hide_cursor(True)
        origin = self.origin
        sign = direction * SCRUB_TRIES[self.tries]
        self.tries += 1

        def run():
            x, y = origin
            try:
                # Cinema 4D leaves a field in text-edit mode after a scrub, and
                # a drag in that mode selects text instead. Escape leaves it.
                _send_key(VK_ESCAPE)
                _move_to(x, y)
                time.sleep(0.008)
                _send_mouse(MOUSEEVENTF_LEFTDOWN)
                time.sleep(0.008)
                for k in range(2, SCRUB_PX + 1, 2):
                    _move_to(x + sign * k, y)
                    time.sleep(0.003)
                time.sleep(0.004)
                _send_mouse(MOUSEEVENTF_LEFTUP)
            finally:
                # Cinema 4D may put the cursor back where its drag began
                # once it sees the button go up; let it, then take over.
                time.sleep(0.006)
                with self.lock:
                    target = (pt[0] + self.held_dx, pt[1] + self.held_dy)
                    self.held_dx = self.held_dy = 0
                    self.out_dx += target[0] - pt[0]
                    self.pt = target
                _move_to(*target)
                self.resume_pt = target
                self._dbg("scrub done", sign * SCRUB_PX, "px")
                self._post("scrubbed")

        t = threading.Thread(target=run, name="ValueLadderScrub")
        t.daemon = True
        t.start()

    def _finish_identify(self):
        if self.phase != IDENTIFYING:
            return
        # Make sure the pointer is where the user left it, whatever the
        # field's drag did with it.
        x, y = self.resume_pt
        if _cursor_pos() != (x, y):
            user32.SetCursorPos(x, y)
        self._hide_cursor(False)
        hits = {}
        for node, before, noisy in zip(self.cands, self.before, self.noisy):
            try:
                if not node.IsAlive():
                    continue
            except Exception:
                continue
            probe = (FOCUS_PROBES[self.focus_attempt - 1]
                     if self.focus_probe and 0 < self.focus_attempt <= len(FOCUS_PROBES) else None)
            for key in _probe_diff(node, before, _snapshot(node), probe):
                if (key[0], key[2]) in noisy:
                    continue
                hits.setdefault(key, []).append((node, before))
        self._dbg("scrub hits", [(k, len(v)) for k, v in hits.items()])
        self.scrub_undo = self._has_probe_undo()
        if self.focus_probe:
            if self.probe_error or c4d.documents.GetActiveDocument() != self.doc:
                self._abort_identify(hits, "ValueLadder: input focus changed; probe cancelled")
                return
            if not hits:
                if self.cancelled or self.released:
                    self._abort_identify(hits, "ValueLadder: no supported numeric change; probe cancelled")
                elif self.focus_attempt < len(FOCUS_PROBES):
                    if self.scrub_undo:
                        # A rounded/clamped expression can still leave a native
                        # undo. Remove only a provably new entry, then reacquire
                        # owners/graphs: DoUndo can replace their handles.
                        self.doc.DoUndo(False)
                        self.scrub_undo = False
                        self._capture_probe_state()
                    self._launch_focus_probe()
                else:
                    self._abort_identify(hits, "ValueLadder: field could not be identified")
                return
            if len(hits) != 1:
                self._abort_identify(hits, "ValueLadder: multiple parameters changed; probe cancelled")
                return
        if not hits:
            # Once more the same way, then the other way in case the value
            # sits at a limit.
            if self.tries < len(SCRUB_TRIES):
                with self.lock:
                    pt = self.pt
                self._launch_scrub(self.scrub_dir, pt)
                return
            c4d.StatusSetText("ValueLadder: no numeric parameter found under the cursor")
            self._close_overlay()
            self._release_capture()
            return
        (pid, kind, comp), found = max(hits.items(), key=lambda kv: len(kv[1]))
        param = Param(found[0][0], pid, kind, comp, snapshot=found[0][1])
        self.param = param
        self.targets = [n for n, _ in found]
        if param.rs_optics is not None:
            param.rs_optics_by_node.update(found)
        if param.rs_focus_by_node:
            param.rs_focus_by_node.update((node, before.focus_distance) for node, before in found)
        # The field's own scrub recorded one undo step holding the original
        # values; every later change rides on it, so the gesture undoes at once.
        self.originals = []
        for _, before in found:
            k, v = before[pid]
            self.originals.append(v[comp] if k == "v" else param.from_snapshot(v))
        if self.focus_probe:
            try:
                probed = param.get(self.targets[0])
                self._dbg("focus matched", param.name, "original", self.originals[0],
                          "probed", probed, "display_delta",
                          (probed - self.originals[0]) * param.scale)
                for node, original in zip(self.targets, self.originals):
                    param.restore(node, original)
                    if abs(param.get(node) - original) > 1e-9:
                        raise RuntimeError("original value was not restored")
            except Exception:
                self._abort_identify(hits, "ValueLadder: original value could not be restored")
                return
            self._dbg("focus original restored", self.originals)
        if param.is_int:
            self.last_rung = INT_LAST_RUNG
            if self.rung > INT_LAST_RUNG:
                self.offset += self.steps * RUNGS[self.rung]
                self.rung = INT_LAST_RUNG
        self.phase = LADDER
        self._dbg("ladder", param.name, "targets", len(self.targets), "rung", self.rung)
        with self.lock:
            self.frozen = False
            pt, out_dx = self.pt, self.out_dx
            released, cancelled = self.released, self.cancelled
        if cancelled:
            self._cancel()
            return
        self.steps = None   # force the first write, which replaces the scrub's value
        self._update(pt, out_dx)
        if released:
            self._finish()

    def readout(self):
        if not self.param or not self.targets:
            return ""
        try:
            return self.param.fmt(self.param.get(self.targets[0]))
        except Exception:
            return ""

    def _update(self, pt, out_dx):
        """Over the ladder the pointer picks the rung; beside it, it scrubs."""
        p = self.param
        if self.overlay_rect is not None and not self.wrap_side:
            self._pick_rung(pt)   # passing over it after a wrap keeps the rung
        travel = out_dx - self.base_dx
        steps = int(travel / PX_PER_STEP)
        if steps != self.steps:
            self.steps = steps
            delta = (self.offset + steps * RUNGS[self.rung]) / p.scale
            for node, orig in zip(self.targets, self.originals):
                try:
                    if (p.kind == "t" or p.rs_optics is not None or p.rs_focus_by_node) and delta == 0:
                        p.restore(node, orig)
                    else:
                        p.set(node, orig + delta)
                except Exception:
                    pass
            self._redraw()
        if self.overlay is not None:
            self.overlay.refresh()

    def _redraw(self, force_event=False):
        """Redraw the viewports right away; refresh the managers less often.

        EventAdd() only queues a redraw, which Cinema 4D merges and delays, so
        the viewport lags behind the drag. DrawViews() draws now. The
        Attribute Manager only needs the occasional EventAdd().
        """
        now = time.time()
        if force_event or now - self.last_draw >= DRAW_INTERVAL:
            self.last_draw = now
            c4d.DrawViews(c4d.DRAWFLAGS_NO_THREAD | c4d.DRAWFLAGS_STATICBREAK)
        if force_event or now - self.last_event >= EVENT_INTERVAL:
            self.last_event = now
            c4d.EventAdd()

    def _finish(self):
        net = self.offset + (self.steps or 0) * RUNGS[self.rung]
        self._dbg("finish net", net)
        self._close_overlay()
        unchanged = False
        try:
            unchanged = all(abs(self.param.get(n) - v) <= 1e-9
                            for n, v in zip(self.targets, self.originals))
        except Exception:
            pass
        if net == 0 or unchanged:
            self._revert()
        self._redraw(force_event=True)
        self._release_capture()

    def _revert(self):
        """Back to the original values. Undo the field's own scrub when it
        recorded a step (no empty step is left); tool settings and the like
        have no undo, so put the values back by hand."""
        if self.scrub_undo:
            self.doc.DoUndo(False)
            return
        for node, orig in zip(self.targets, self.originals):
            try:
                self.param.restore(node, orig)
            except Exception:
                pass

    def _cancel(self):
        self._dbg("cancel")
        self._close_overlay()
        self._revert()
        self._redraw(force_event=True)
        self._release_capture()

    def _open_overlay(self):
        """Show a finished bitmap in a popup which never has a native frame."""
        self.overlay = LadderOverlay(self)
        self.overlay_rect = None
        x, y = self.origin
        w, h = OVERLAY_W, RUNG_PX * (len(RUNGS) + 1)
        left = x - w // 2
        top = y - RUNG_PX * self.rung - RUNG_PX // 2
        try:
            self.overlay.Open(left, top, w, h)
            if not self.overlay.Show():
                raise RuntimeError("could not show native ladder window")
            self.overlay_rect = (left, top, w, h)
            with self.lock:
                self.ladder_rect = (left, top, left + w, top + h)
        except Exception as error:
            self._dbg("overlay failed", error)
            self._close_overlay()

    def _close_overlay(self):
        self.overlay_rect = None
        if self.overlay is not None:
            try:
                self.overlay.Close()
            except Exception:
                pass
            self.overlay = None


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------
_controller = None


class ValueLadderMessage(c4d.plugins.MessageData):
    def CoreMessage(self, id, bc):
        if id == PLUGIN_ID and _controller is not None:
            _controller.pump()
        return True


def _prefs():
    world = c4d.GetWorldContainerInstance()
    bc = world.GetContainerInstance(PLUGIN_ID)
    if bc is None:
        world.SetContainer(PLUGIN_ID, c4d.BaseContainer())
        bc = world.GetContainerInstance(PLUGIN_ID)
    return bc


def _enable_at_startup():
    return _prefs().GetBool(PREFS_ENABLE_AT_STARTUP, True)


class ValueLadderPrefs(c4d.plugins.PreferenceData):
    """The ValueLadder page in Edit > Preferences."""

    def InitValues(self, descId, description=None):
        if descId[0].id == PREFS_ENABLE_AT_STARTUP:
            self.InitPreferenceValue(PREFS_ENABLE_AT_STARTUP, True, description, descId, _prefs())
        return True

    def GetDDescription(self, node, description, flags):
        if not description.LoadDescription(PREFS_DESCRIPTION):
            return False
        if flags & c4d.DESCFLAGS_DESC_NEEDDEFAULTVALUE:
            self.InitValues(c4d.DescID(c4d.DescLevel(PREFS_ENABLE_AT_STARTUP, c4d.DTYPE_BOOL, 0)), description)
        return True, flags | c4d.DESCFLAGS_DESC_LOADED

    def GetDParameter(self, node, id, flags):
        if id[0].id == PREFS_ENABLE_AT_STARTUP:
            return True, _enable_at_startup(), flags | c4d.DESCFLAGS_GET_PARAM_GET
        return False

    def SetDParameter(self, node, id, data, flags):
        if id[0].id == PREFS_ENABLE_AT_STARTUP:
            _prefs().SetBool(PREFS_ENABLE_AT_STARTUP, bool(data))
            # Apply right away too, so the checkbox never disagrees with the plugin.
            if _controller is not None:
                _controller.enabled = bool(data)
            return True, flags | c4d.DESCFLAGS_SET_PARAM_SET
        return False


class ValueLadderToggle(c4d.plugins.CommandData):
    def Execute(self, doc):
        if _controller is not None:
            _controller.enabled = not _controller.enabled
            c4d.StatusSetText("ValueLadder %s" % ("enabled" if _controller.enabled else "disabled"))
        return True

    def GetState(self, doc):
        on = _controller is not None and _controller.enabled
        return c4d.CMD_ENABLED | (c4d.CMD_VALUE if on else 0)


def PluginMessage(id, data):
    if _controller is None:
        return False
    if id == c4d.C4DPL_PROGRAM_STARTED:
        _controller.enabled = _enable_at_startup()
    elif id == c4d.C4DPL_ENDACTIVITY:
        _controller.stop()
    return False


def _wake():
    c4d.SpecialEventAdd(PLUGIN_ID)


if __name__ == "__main__":
    if IS_WINDOWS:
        _controller = Controller(_wake)
        c4d.plugins.RegisterMessagePlugin(PLUGIN_ID, "ValueLadder", 0, ValueLadderMessage())
        c4d.plugins.RegisterCommandPlugin(COMMAND_ID, "ValueLadder", 0, None,
                                          "Toggle middle-mouse ladder scrubbing",
                                          ValueLadderToggle())
        c4d.plugins.RegisterPreferencePlugin(PREFS_ID, ValueLadderPrefs, "ValueLadder",
                                             PREFS_DESCRIPTION, 0, 0)
        _controller.start()
    else:
        print("[ValueLadder] only Windows is supported in this version")
