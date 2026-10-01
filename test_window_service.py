"""Unit tests for services/window_service.py."""

import unittest
from services.window_service import (
    WindowService,
    calc_avatar_anchor,
    calc_panel_anchor,
    clamp_to_work_area,
)


class TestWindowService(unittest.TestCase):
    def setUp(self):
        # 1920x1080 work area with 40px taskbar on bottom
        self.mock_work_area = (0, 0, 1920, 1040)

    def test_clamp_to_work_area_within_bounds(self):
        x, y = clamp_to_work_area(500, 300, 380, 580, work_area=self.mock_work_area)
        self.assertEqual(x, 500)
        self.assertEqual(y, 300)

    def test_clamp_to_work_area_exceeds_right_bottom(self):
        # Window placed at 1800, 900 (would bleed past 1920x1040)
        x, y = clamp_to_work_area(1800, 900, 380, 580, work_area=self.mock_work_area)
        self.assertEqual(x, 1920 - 380 - 16)  # right edge clamped
        self.assertEqual(y, 1040 - 580 - 16)  # bottom edge clamped

    def test_clamp_to_work_area_negative_coords(self):
        x, y = clamp_to_work_area(-500, -200, 380, 580, work_area=self.mock_work_area)
        self.assertEqual(x, 16)  # Left margin
        self.assertEqual(y, 16)  # Top margin

    def test_clamp_to_work_area_defaults_none(self):
        x, y = clamp_to_work_area(None, None, 380, 580, work_area=self.mock_work_area)
        self.assertEqual(x, 1920 - 380 - 16)  # Right edge default
        self.assertEqual(y, 40)  # Top offset default

    def test_calc_avatar_anchor(self):
        # Panel at (1000, 200) with width 380, avatar size 72
        # Avatar should be placed at 1000 + 380 - 72 = 1308, y = 200
        ax, ay = calc_avatar_anchor(1000, 200, 380, 72, work_area=self.mock_work_area)
        self.assertEqual(ax, 1308)
        self.assertEqual(ay, 200)

    def test_calc_panel_anchor(self):
        # Avatar at (1308, 200), size 72, panel width 380, height 580
        # Panel should align top-right: px = 1308 + 72 - 380 = 1000, py = 200
        px, py = calc_panel_anchor(1308, 200, 72, 380, 580, work_area=self.mock_work_area)
        self.assertEqual(px, 1000)
        self.assertEqual(py, 200)

    def test_win32_helpers_safe_without_hwnd(self):
        # Should not raise exception
        WindowService.apply_circular_region(None, True)
        WindowService.apply_circular_region(None, False)
        WindowService.start_resize(None, "right")
        WindowService.subclass_minmax(None)

    def test_get_window_scale_default(self):
        scale = WindowService.get_window_scale(None)
        self.assertEqual(scale, 1.0)


if __name__ == "__main__":
    unittest.main()
