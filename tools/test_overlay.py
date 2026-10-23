"""C4D/Win32 integration checks for the ladder's first visible frame.

Creates only temporary plugin windows; never injects input or edits a scene.
"""
import ctypes
import unittest

import c4d

from test_focus_probe import _load_plugin


def main():
    plugin = _load_plugin()
    class OverlayChecks(unittest.TestCase):
        def setUp(self):
            self.foreground = plugin.user32.GetForegroundWindow()
            self.ctrl = plugin.Controller(lambda: None)
            self.ctrl.origin = (400, 300)

        def tearDown(self):
            self.ctrl._close_overlay()
            if self.foreground:
                plugin.user32.SetForegroundWindow(self.foreground)

        def test_window_is_born_hidden_and_borderless(self):
            overlay = plugin.LadderOverlay(self.ctrl)
            try:
                self.assertTrue(overlay.Open(400, 300, 52, 416))
                hwnd = overlay.native_hwnd
                self.assertFalse(plugin.user32.IsWindowVisible(hwnd))
                self.assertFalse(plugin.user32.GetWindowLongW(hwnd, plugin.GWL_STYLE) & plugin.WS_FRAME_BITS)
                self.assertTrue(overlay.bitmap)
                self.assertTrue(overlay.Show())
                self.assertEqual(plugin.user32.GetForegroundWindow(), self.foreground)
            finally:
                overlay.Close()

        def test_reveal_has_no_frame_and_preserves_foreground(self):
            self.ctrl._open_overlay()
            self.assertIsNotNone(self.ctrl.overlay_rect, list(self.ctrl.log))
            hwnd = self.ctrl.overlay.native_hwnd
            self.assertFalse(plugin.user32.GetWindowLongW(hwnd, plugin.GWL_STYLE) & plugin.WS_FRAME_BITS)
            ex = plugin.user32.GetWindowLongW(hwnd, plugin.GWL_EXSTYLE)
            self.assertTrue(ex & plugin.WS_EX_TRANSPARENT)
            self.assertTrue(ex & plugin.WS_EX_NOACTIVATE)
            left, top, width, height = self.ctrl.overlay_rect
            hit = plugin.user32.WindowFromPoint(plugin.wt.POINT(left + width // 2, top + height // 2))
            self.assertNotEqual(hit, hwnd, 'Ladder must not intercept the numeric-field click')
            self.assertEqual(plugin.user32.GetForegroundWindow(), self.foreground)
            rect = plugin.wt.RECT()
            self.assertTrue(plugin.user32.GetWindowRect(hwnd, ctypes.byref(rect)))
            self.assertEqual((rect.right - rect.left, rect.bottom - rect.top), (52, 416))
            client = plugin.wt.RECT()
            plugin.user32.GetClientRect.argtypes = [plugin.wt.HWND, ctypes.POINTER(plugin.wt.RECT)]
            self.assertTrue(plugin.user32.GetClientRect(hwnd, ctypes.byref(client)))
            self.assertEqual((client.right, client.bottom), (52, 416))
            self.assertEqual(self.ctrl.overlay_rect[2:], (52, 416))

        def resources(self):
            plugin.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            plugin.user32.GetGuiResources.argtypes = [ctypes.c_void_p, plugin.wt.DWORD]
            process = plugin.kernel32.GetCurrentProcess()
            return tuple(plugin.user32.GetGuiResources(process, kind) for kind in (0, 1))

        def test_repeated_open_refresh_close_releases_resources(self):
            plugin.user32.IsWindow.argtypes = [plugin.wt.HWND]
            # Warm native font and drawing caches before taking the baseline.
            self.ctrl._open_overlay()
            self.ctrl._close_overlay()
            baseline = self.resources()
            for _ in range(50):
                self.ctrl._open_overlay()
                self.assertIsNotNone(self.ctrl.overlay_rect, list(self.ctrl.log))
                hwnd = self.ctrl.overlay.native_hwnd
                self.ctrl.rung = (self.ctrl.rung + 1) % len(plugin.RUNGS)
                self.ctrl.overlay.refresh()
                self.assertFalse(plugin.user32.GetWindowLongW(hwnd, plugin.GWL_STYLE) & plugin.WS_FRAME_BITS)
                self.ctrl._close_overlay()
                self.assertFalse(plugin.user32.IsWindow(hwnd))
            after = self.resources()
            self.assertLessEqual(after[0], baseline[0] + 2, (baseline, after))
            self.assertLessEqual(after[1], baseline[1] + 2, (baseline, after))

        def test_drawing_failure_releases_partial_window(self):
            from unittest import mock
            before = self.resources()
            overlay = plugin.LadderOverlay(self.ctrl)
            with mock.patch.object(overlay, '_draw', side_effect=RuntimeError('draw failed')):
                with self.assertRaisesRegex(RuntimeError, 'draw failed'):
                    overlay.Open(400, 300, 52, 416)
            self.assertIsNone(overlay.native_hwnd)
            self.assertIsNone(overlay.bitmap)
            self.assertIsNone(overlay.font)
            after = self.resources()
            self.assertLessEqual(after[0], before[0] + 2, (before, after))
            self.assertLessEqual(after[1], before[1] + 2, (before, after))

        def test_every_submitted_frame_is_fully_drawn(self):
            from unittest import mock
            plugin.gdi32.GetPixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
            plugin.gdi32.GetPixel.restype = plugin.wt.DWORD
            present = plugin.user32.UpdateLayeredWindow
            observed = []
            def inspect(hwnd, screen, destination, size, dc, source, key, blend, flags):
                self.assertEqual(flags, plugin.ULW_OPAQUE)
                self.assertEqual(plugin.gdi32.GetPixel(dc, 0, 0), 0x212121)
                y = self.ctrl.rung * plugin.RUNG_PX + 2
                self.assertEqual(plugin.gdi32.GetPixel(dc, 3, y), 77 | (87 << 8) | (158 << 16))
                observed.append(self.ctrl.rung)
                return present(hwnd, screen, destination, size, dc, source, key, blend, flags)
            with mock.patch.object(plugin.user32, 'UpdateLayeredWindow', side_effect=inspect):
                self.ctrl._open_overlay()
                for index in range(300):
                    self.ctrl.rung = index % len(plugin.RUNGS)
                    self.ctrl.readout = lambda value=index: str(value * .001)
                    self.ctrl.overlay.refresh()
            self.assertEqual(len(observed), 301)

        def test_failed_presentation_retains_last_frame(self):
            from unittest import mock
            self.ctrl._open_overlay()
            overlay = self.ctrl.overlay
            previous_bitmap, previous_frame = overlay.bitmap, overlay.frame_key
            self.ctrl.rung = (self.ctrl.rung + 1) % len(plugin.RUNGS)
            with mock.patch.object(plugin.user32, 'UpdateLayeredWindow', return_value=0):
                with self.assertRaisesRegex(RuntimeError, 'present ladder frame'):
                    overlay.refresh()
            self.assertEqual(overlay.bitmap, previous_bitmap)
            self.assertEqual(overlay.frame_key, previous_frame)
            self.assertTrue(plugin.user32.IsWindowVisible(overlay.native_hwnd))
            overlay.refresh()
            self.assertNotEqual(overlay.frame_key, previous_frame)

        def test_unchanged_frames_do_not_repaint(self):
            from unittest import mock
            self.ctrl._open_overlay()
            with mock.patch.object(plugin.user32, 'UpdateLayeredWindow') as present:
                for _ in range(100):
                    self.ctrl.overlay.refresh()
            present.assert_not_called()

        def test_refresh_does_not_use_control_background_painting(self):
            from unittest import mock
            from contextlib import ExitStack
            with ExitStack() as patches:
                for name in ('SendMessageW', 'InvalidateRect', 'UpdateWindow', 'SetLayeredWindowAttributes'):
                    patches.enter_context(mock.patch.object(plugin.user32, name,
                        side_effect=AssertionError('Legacy paint path was used: ' + name)))
                self.ctrl._open_overlay()
                self.assertIsNotNone(self.ctrl.overlay_rect, list(self.ctrl.log))
                self.ctrl.rung = 4
                self.ctrl.overlay.refresh()
                self.ctrl._close_overlay()

    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(OverlayChecks))
    if not result.wasSuccessful():
        raise AssertionError('Overlay SDK checks failed')
    return result


if __name__ == '__main__':
    main()
