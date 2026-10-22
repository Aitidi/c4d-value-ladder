"""ValueLadder - Houdini-style middle-mouse "ladder" value scrubbing for Cinema 4D.

Hold the middle mouse button over a numeric field in the Attribute Manager (or
any other manager that shows parameter fields), move up/down to pick a step
size, then move left/right to change the value. Release to commit (one undo
step), right-click while holding to cancel.

How it works (Windows only):
  1. A low-level mouse hook sees the middle button go down. If the pixels under
     the cursor look like a Cinema 4D edit field, the click is swallowed and a
     ladder opens centred on the cursor, like Houdini's. Nothing changes yet.
  2. The pointer moves freely: over the ladder it picks the rung, beside it,
     left/right travel changes the value by that rung's step.
  3. Cinema 4D has no API that says which parameter is under the cursor. The
     first time the pointer leaves the ladder, the plugin lets the field scrub
     itself natively by a few pixels, the way the user is dragging, and finds
     the parameter that changed by diffing the selected nodes. That scrub
     leaves one undo step holding the original values; every later change
     rides on it, so the whole gesture undoes in one go.
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

__version__ = "0.6.2"

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
SCRUB_TRIES = (1, 1, -1)  # direction of each try, relative to the user's drag
MAX_CANDIDATES = 500    # nodes snapshotted per gesture
EVENT_INTERVAL = 0.1    # seconds between manager refreshes while scrubbing
OVERLAY_W = 52          # width of the ladder, in pixels (Houdini-like)
RUNG_PX = OVERLAY_W     # rungs are square
EDGE_PX = 1             # how close to a screen edge the pointer wraps
WARP_IN_PX = 1          # where it comes back in: this far inside the other edge
DIALOG_GRIP_PT = 17     # height of the menu grip C4D draws atop async dialogs, at 96 dpi

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
    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
    user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    user32.IsWindowVisible.argtypes = [wt.HWND]
    user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = wt.LONG
    user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, wt.LONG]
    user32.SetWindowLongW.restype = wt.LONG
    user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wt.UINT]
    user32.GetForegroundWindow.restype = wt.HWND
    user32.SetForegroundWindow.argtypes = [wt.HWND]
    user32.SetLayeredWindowAttributes.argtypes = [wt.HWND, wt.DWORD, ctypes.c_ubyte, wt.DWORD]
    user32.LoadCursorW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
    user32.LoadCursorW.restype = ctypes.c_void_p
    user32.CopyIcon.argtypes = [ctypes.c_void_p]
    user32.CopyIcon.restype = ctypes.c_void_p
    user32.SetSystemCursor.argtypes = [ctypes.c_void_p, wt.DWORD]
    user32.SystemParametersInfoW.argtypes = [wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT]
    user32.ShowCursor.argtypes = [wt.BOOL]
    user32.ShowCursor.restype = ctypes.c_int
    user32.SetWindowRgn.argtypes = [wt.HWND, ctypes.c_void_p, wt.BOOL]
    user32.GetDpiForWindow.argtypes = [wt.HWND]
    user32.GetDpiForWindow.restype = wt.UINT
    gdi32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
    gdi32.CreateRectRgn.restype = ctypes.c_void_p
    user32.WindowFromPoint.argtypes = [wt.POINT]
    user32.WindowFromPoint.restype = wt.HWND
    user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
    user32.GetAncestor.restype = wt.HWND
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
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
LWA_ALPHA = 0x2
SPI_SETCURSORS = 0x0057
HWND_TOPMOST = wt.HWND(-1) if sys.platform.startswith("win") else None
SWP_SHOWWINDOW, SWP_FRAMECHANGED, SWP_NOACTIVATE = 0x0040, 0x0020, 0x0010
SRCCOPY = 0x00CC0020

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_ESCAPE = 0x1B
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


def _send_key(vk):
    for flags in (0, KEYEVENTF_KEYUP):
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.u.ki = KEYBDINPUT(vk, 0, flags, 0, INJECT_MAGIC)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


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
MAX_FIELD_OWNERS = 50   # selected nodes searched for field lists
MAX_GRAPH_NODES = 30    # node-material nodes read per material
KEY_SCAN_BUDGET = 0.015 # seconds spent looking for selected keys


def _fps(node):
    try:
        doc = node if isinstance(node, c4d.documents.BaseDocument) else node.GetDocument()
        if doc is None:
            doc = c4d.documents.GetActiveDocument()
        return doc.GetFps()
    except Exception:
        return 30


def _snapshot(node):
    """Every numeric parameter of node, keyed by its id (an int) or, for
    nested parameters such as user data, by the tuple of level ids."""
    if hasattr(node, "vl_snapshot"):
        return node.vl_snapshot()
    snap = {}
    try:
        data = node.GetDataInstance()
    except Exception:
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
    return snap


def _snapshot_description(node, snap):
    try:
        desc = node.GetDescription(c4d.DESCFLAGS_DESC_NONE)
        entries = iter(desc)
    except Exception:
        return
    fps = None
    while True:
        try:
            _, descid, _ = next(entries)
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
        kind = _DESC_KINDS.get(descid[depth - 1].dtype)
        if kind is None or key in snap:
            continue
        try:
            v = node.GetParameter(descid, c4d.DESCFLAGS_GET_NONE)
            if kind == "t":
                if fps is None:
                    fps = _fps(node)
                snap[key] = ("t", float(v.GetFrame(fps)))
            elif kind == "v":
                snap[key] = ("v", (v.x, v.y, v.z))
            elif kind == "r":
                snap[key] = ("r", float(v))
            else:
                snap[key] = ("l", int(v))
        except Exception:
            pass


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
        elif abs(new - old) > 1e-9:
            yield key, kind, None


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
    """The input ports of a node in a node material (Redshift, Standard...).
    Keys are port paths; values are set in transactions that add no undo
    step, so the field's own scrub stays the one undo for the gesture."""

    def __init__(self, material, graph, node):
        self.material, self.graph = material, graph
        self.path = node.GetPath()
        self.types = {}

    def _ports(self, port, out):
        children = []
        port.GetChildren(children, maxon.NODE_KIND.INPORT)
        for child in children:
            out.append(child)
            self._ports(child, out)
        return out

    def vl_snapshot(self):
        snap = {}
        try:
            node = self.graph.GetNode(self.path)
            ports = self._ports(node.GetInputs(), [])
        except Exception:
            return snap
        for port in ports:
            try:
                v = port.GetPortValue()
                name = type(v).__name__
                if name.startswith("Float"):
                    snap[str(port.GetPath())] = ("r", float(v))
                elif name.startswith("Int") or name.startswith("UInt"):
                    snap[str(port.GetPath())] = ("l", int(v))
                else:
                    continue
                self.types[str(port.GetPath())] = type(v)
            except Exception:
                pass
        return snap

    def vl_info(self, key):
        bc = c4d.BaseContainer()
        bc[c4d.DESC_NAME] = key.rsplit(".", 1)[-1]
        return bc

    def GetParameter(self, descid, flags):
        v = self.graph.GetNode(maxon.NodePath(descid[0])).GetPortValue()
        return float(v) if type(v).__name__.startswith("Float") else int(v)

    def SetParameter(self, descid, value, flags):
        key = descid[0]
        port = self.graph.GetNode(maxon.NodePath(key))
        typ = self.types.get(key)
        data = typ(value) if typ is not None else value
        undo = maxon.DataDictionary()
        undo.Set(maxon.nodes.UndoMode, maxon.nodes.UNDO_MODE.NONE)
        with self.graph.BeginTransaction(undo) as transaction:
            port.SetPortValue(data)
            transaction.Commit()
        return True

    def IsAlive(self):
        return self.material.IsAlive()

    def GetName(self):
        return self.material.GetName()

    def GetDocument(self):
        return self.material.GetDocument()


def _node_ports(materials):
    """The nodes selected in each node material's graph (all of them when
    none is selected)."""
    found = []
    spaces = []
    try:
        spaces.append(c4d.GetActiveNodeSpaceId())
    except Exception:
        pass
    for mat in materials:
        try:
            ref = mat.GetNodeMaterialReference()
        except Exception:
            continue
        for space in spaces:
            try:
                if not ref.HasSpace(space):
                    continue
                graph = ref.GetGraph(space)
                if graph.IsNullValue():
                    continue
                selected = []
                maxon.GraphModelHelper.GetSelectedNodes(
                    graph, maxon.NODE_KIND.NODE, lambda n: selected.append(n) or True)
                if not selected:
                    graph.GetViewRoot().GetChildren(selected, maxon.NODE_KIND.NODE)
                found.extend(_NodePorts(mat, graph, n) for n in selected[:MAX_GRAPH_NODES])
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


def _candidates(doc, floating=False):
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
    nodes.extend(_shaders(primary))
    nodes.extend(_xpresso_nodes(primary))
    nodes.extend(_field_layers(primary + nodes[len(primary):], doc))
    nodes.extend(_node_ports([n for n in primary if isinstance(n, c4d.BaseMaterial)]))
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

    def __init__(self, node, pid, kind, comp):
        self.pid, self.kind, self.comp = pid, kind, comp
        self.descid = _descid(pid, kind, comp if kind == "v" else None)
        self.is_int = kind in ("l", "t")   # times are scrubbed in whole frames
        self.fps = _fps(node)
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
            return float(v.GetFrame(self.fps))
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
            return float(v.GetFrame(self.fps))
        return v

    def set(self, node, value):
        if self.vmin is not None:
            value = max(self.vmin, value)
        if self.vmax is not None:
            value = min(self.vmax, value)
        if self.is_int:
            value = int(round(value))
        data = c4d.BaseTime(value, self.fps) if self.kind == "t" else value
        node.SetParameter(self.descid, data, c4d.DESCFLAGS_SET_USERINTERACTION)
        return value

    def fmt(self, internal):
        v = internal * self.scale
        if self.is_int:
            return "%d" % int(round(v)) + self.suffix
        return ("%.4f" % v).rstrip("0").rstrip(".") + self.suffix


# ---------------------------------------------------------------------------
# Overlay
# ---------------------------------------------------------------------------
class LadderArea(c4d.gui.GeUserArea):
    def __init__(self, ctrl):
        c4d.gui.GeUserArea.__init__(self)
        self.ctrl = ctrl

    def DrawMsg(self, x1, y1, x2, y2, msg):
        self.OffScreenOn()
        ctrl = self.ctrl
        w, h = self.GetWidth(), self.GetHeight()
        bg = c4d.Vector(0.13)
        self.DrawSetPen(bg)
        self.DrawRectangle(0, 0, w, h)
        rungs = ctrl.rungs or []
        n = len(rungs)
        if not n:
            return
        rh = h // (n + 1)
        self.DrawSetFont(c4d.FONT_DEFAULT)
        for i, r in enumerate(rungs):
            y = i * rh
            active = i == ctrl.rung
            usable = i <= ctrl.last_rung
            fill = (c4d.Vector(0.30, 0.34, 0.62) if active else
                    c4d.Vector(0.20) if usable else c4d.Vector(0.15))
            self.DrawSetPen(fill)
            self.DrawRectangle(2, y + 1, w - 3, y + rh - 2)
            label = ("%g" % r)
            fg = c4d.Vector(1.0) if active else c4d.Vector(0.75) if usable else c4d.Vector(0.4)
            self.DrawSetTextCol(fg, fill)
            self.DrawText(label, w // 2 - self.DrawGetTextWidth(label) // 2,
                          y + rh // 2 - self.DrawGetFontHeight() // 2)
        # Value readout under the rungs.
        y = n * rh
        self.DrawSetPen(c4d.Vector(0.08))
        self.DrawRectangle(2, y + 1, w - 3, h - 2)
        text = ctrl.readout()
        self.DrawSetTextCol(c4d.Vector(1.0, 0.85, 0.4), c4d.Vector(0.08))
        self.DrawText(text, w // 2 - self.DrawGetTextWidth(text) // 2,
                      y + rh // 2 - self.DrawGetFontHeight() // 2)


OVERLAY_TITLE = "ValueLadder ladder"


class LadderOverlay(c4d.gui.GeDialog):
    def __init__(self, ctrl):
        c4d.gui.GeDialog.__init__(self)
        self.area = LadderArea(ctrl)

    def CreateLayout(self):
        self.SetTitle(OVERLAY_TITLE)
        self.AddUserArea(1000, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT)
        self.AttachUserArea(self.area, 1000)
        return True

    def refresh(self):
        self.area.Redraw()


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------
IDLE, PENDING, IDENTIFYING, LADDER = 0, 1, 2, 3


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
        self._close_overlay()
        if self.cursors_swapped:
            _cursors_scrub(False)
            self.cursors_swapped = False

    def _hook_loop(self):
        self.hook_tid = kernel32.GetCurrentThreadId()
        hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._hook_proc, None, 0)
        if not hook:
            print("[ValueLadder] SetWindowsHookEx failed: %d" % ctypes.get_last_error())
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        user32.UnhookWindowsHookEx(hook)

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

    def _on_mouse(self, msg, ms):
        """Return True to swallow the event."""
        if not self.capturing:
            if msg == WM_MBUTTONDOWN and self.enabled and self.phase == IDLE:
                x, y = ms.pt.x, ms.pt.y
                over = self._over_field(x, y)
                self._dbg("mmb down", x, y, "field" if over else "pass")
                if over:
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

    def _begin(self):
        """Middle button down on a field: show the ladder, touch nothing yet."""
        self.doc = c4d.documents.GetActiveDocument()
        self.cands = _candidates(self.doc, self._floating(self.origin))
        self.before = [_snapshot(n) for n in self.cands]
        self.noisy = [set() for _ in self.cands]
        self.undo_before = self.doc.GetUndoPtr()
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
        self._dbg("pending at", self.origin, "candidates", len(self.cands))
        self._open_overlay()
        if self.overlay_rect is None:
            self._release_capture()
            return
        _cursors_scrub(True)
        self.cursors_swapped = True

    def _floating(self, pt):
        """True when the press is in a window apart from the main one."""
        try:
            hwnd = user32.GetAncestor(user32.WindowFromPoint(wt.POINT(*pt)), 2)  # GA_ROOT
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            return "Cinema 4D" not in title.value
        except Exception:
            return False

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

    def _launch_scrub(self, direction, pt):
        """First time the pointer leaves the ladder: let the field under the
        press point scrub itself a few pixels, the same way the user is
        dragging; the diff names the parameter. The ladder lets clicks
        through, so the field gets them."""
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
            for key in _diff(before, _snapshot(node)):
                if (key[0], key[2]) in noisy:
                    continue
                hits.setdefault(key, []).append((node, before))
        self._dbg("scrub hits", [(k, len(v)) for k, v in hits.items()])
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
        undo = self.doc.GetUndoPtr()
        self.scrub_undo = undo is not None and undo != self.undo_before
        param = Param(found[0][0], pid, kind, comp)
        self.param = param
        self.targets = [n for n, _ in found]
        # The field's own scrub recorded one undo step holding the original
        # values; every later change rides on it, so the gesture undoes at once.
        self.originals = []
        for _, before in found:
            k, v = before[pid]
            self.originals.append(v[comp] if k == "v" else v)
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
        c4d.DrawViews(c4d.DRAWFLAGS_NO_THREAD | c4d.DRAWFLAGS_STATICBREAK)
        now = time.time()
        if force_event or now - self.last_event >= EVENT_INTERVAL:
            self.last_event = now
            c4d.EventAdd()

    def _finish(self):
        net = self.offset + (self.steps or 0) * RUNGS[self.rung]
        self._dbg("finish net", net)
        self._close_overlay()
        if net == 0:
            self._revert()
        c4d.EventAdd()
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
                self.param.set(node, orig)
            except Exception:
                pass

    def _cancel(self):
        self._dbg("cancel")
        self._close_overlay()
        self._revert()
        c4d.EventAdd()
        self._release_capture()

    def _open_overlay(self):
        """Open the ladder as a borderless topmost window centred on the cursor,
        with the current rung under the pointer.

        It has to be a normal async dialog (a popup closes at the next click);
        the frame is stripped and the window placed through Win32 so its size
        and position are in physical pixels, the same space as the hook's.
        """
        self.overlay = LadderOverlay(self)
        self.overlay_rect = None
        # Opening a dialog activates it; hand activation straight back so the
        # next scrub's click isn't spent re-activating the main window.
        prev = user32.GetForegroundWindow()
        x, y = self.origin
        w, h = OVERLAY_W, RUNG_PX * (len(RUNGS) + 1)
        left = x - w // 2
        top = y - RUNG_PX * self.rung - RUNG_PX // 2
        try:
            self.overlay.Open(c4d.DLG_TYPE_ASYNC, xpos=left, ypos=top, defaultw=w, defaulth=h)
        except Exception:
            self.overlay = None
            return
        hwnd = self._find_overlay_hwnd()
        if not hwnd:
            if prev:
                user32.SetForegroundWindow(prev)
            return
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        user32.SetWindowLongW(hwnd, GWL_STYLE, (style & ~WS_FRAME_BITS) | WS_POPUP)
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        # Layered + transparent: clicks fall through to the field underneath,
        # which the identifying scrub needs; the ladder only has to be seen.
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
                              | WS_EX_LAYERED | WS_EX_TRANSPARENT)
        user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
        # Async dialogs draw a small menu grip above their content; push it
        # above the visible region so only the ladder shows.
        try:
            grip = int(round(DIALOG_GRIP_PT * user32.GetDpiForWindow(hwnd) / 96.0))
        except Exception:
            grip = DIALOG_GRIP_PT
        user32.SetWindowPos(hwnd, HWND_TOPMOST, left, top - grip, w, h + grip,
                            SWP_FRAMECHANGED | SWP_SHOWWINDOW | SWP_NOACTIVATE)
        user32.SetWindowRgn(hwnd, gdi32.CreateRectRgn(0, grip, w, h + grip), True)
        if prev and prev != hwnd:
            user32.SetForegroundWindow(prev)
        self.overlay_rect = (left, top, w, h)
        with self.lock:
            self.ladder_rect = (left, top, left + w, top + h)

    def _find_overlay_hwnd(self):
        found = []

        def cb(hwnd, _):
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == self.pid and user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(64)
                user32.GetWindowTextW(hwnd, buf, 64)
                if buf.value == OVERLAY_TITLE:
                    found.append(hwnd)
            return True

        user32.EnumWindows(WNDENUMPROC(cb), 0)
        return found[0] if found else None

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
