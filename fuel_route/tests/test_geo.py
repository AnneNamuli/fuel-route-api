"""Tests for the US border check."""

from django.test import SimpleTestCase

from fuel_route.services.geo import in_usa, simplify_line


class InUsaTests(SimpleTestCase):
    """The US border check used for coordinate input."""

    def test_us_points_including_coast_border_towns_alaska_and_hawaii(self):
        """Points inside the US, on the coast or at the border count as US."""
        for name, point in {
            'Detroit': (42.3314, -83.0458),
            'San Ysidro': (32.5556, -117.0470),
            'Key West': (24.5551, -81.78),
            'Point Roberts': (48.9854, -123.0682),
            'Anchorage': (61.2181, -149.9003),
            'Honolulu': (21.3069, -157.8583),
        }.items():
            with self.subTest(name):
                self.assertTrue(in_usa(*point))

    def test_neighbouring_countries_and_open_sea_are_rejected(self):
        """Canadian and Mexican cities near the border, and points at sea, are not US."""
        for name, point in {
            'Toronto': (43.6532, -79.3832),
            'Vancouver': (49.2827, -123.1207),
            'Tijuana': (32.5149, -117.0382),
            'Mexicali': (32.6245, -115.4523),
            'London': (51.5, -0.12),
            'Atlantic': (35.0, -60.0),
        }.items():
            with self.subTest(name):
                self.assertFalse(in_usa(*point))


class SimplifyLineTests(SimpleTestCase):
    """Douglas-Peucker simplification of the drawn route."""

    def test_straight_stretch_collapses_to_its_ends(self):
        """Points on a straight line are dropped; the ends stay."""
        line = [[x / 10, 0.0] for x in range(101)]
        self.assertEqual(simplify_line(line, 0.001), [[0.0, 0.0], [10.0, 0.0]])

    def test_corners_are_kept(self):
        """A bend further than the tolerance from the straight line is kept."""
        line = [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [2.0, 1.0], [2.0, 2.0]]
        self.assertEqual(simplify_line(line, 0.001), [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0]])

    def test_short_lines_are_unchanged(self):
        """Two points can't be simplified."""
        self.assertEqual(simplify_line([[0.0, 0.0], [1.0, 1.0]], 0.5), [[0.0, 0.0], [1.0, 1.0]])
