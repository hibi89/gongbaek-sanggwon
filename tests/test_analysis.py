from __future__ import annotations

import math
import unittest

from scripts.analyze_gap import EARTH_RADIUS_METERS, bounding_box, count_points, haversine_m, ranked_categories
from scripts.korea_coordinates import is_korea_plausible
from scripts.taxonomy_integrity import canonical_taxonomy_hash


class AnalysisRankingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.categories = [
            {"code": "A001", "name": "생활 업종", "abc_tag": "A"},
            {"code": "B001", "name": "시설 업종", "abc_tag": "B"},
            {"code": "C001", "name": "전문 업종", "abc_tag": "C"},
        ]

    def test_exact_one_kilometer_boundary_is_included(self) -> None:
        boundary_lon = math.degrees(1000 / EARTH_RADIUS_METERS)
        distance = haversine_m(0.0, 0.0, boundary_lon, 0.0)
        self.assertAlmostEqual(distance, 1000.0, places=9)

        counts, _, included = count_points([("A001", boundary_lon, 0.0)], self.categories, 0.0, 0.0)
        self.assertEqual(included, 1)
        self.assertEqual(counts["A001"], [1, 1, 1, 1, 1])

    def test_a_point_just_outside_one_kilometer_is_not_in_n1(self) -> None:
        beyond_lon = math.degrees(1000.001 / EARTH_RADIUS_METERS)
        counts, _, included = count_points([("A001", beyond_lon, 0.0)], self.categories, 0.0, 0.0)
        self.assertEqual(included, 1)
        self.assertEqual(counts["A001"], [0, 1, 1, 1, 1])

    def test_zero_count_categories_are_retained(self) -> None:
        counts, _, included = count_points([("A001", 0.0, 0.0)], self.categories, 0.0, 0.0)
        self.assertEqual(included, 1)
        self.assertEqual(counts["B001"], [0, 0, 0, 0, 0])
        self.assertEqual(counts["C001"], [0, 0, 0, 0, 0])

    def test_rank_order_uses_5km_to_1km_then_code(self) -> None:
        categories = [
            {"code": "A003", "name": "셋", "abc_tag": "A"},
            {"code": "A001", "name": "하나", "abc_tag": "A"},
            {"code": "B002", "name": "둘", "abc_tag": "B"},
        ]
        counts = {
            "A003": [0, 0, 0, 1, 2],
            "A001": [0, 0, 0, 1, 2],
            "B002": [0, 0, 1, 1, 2],
        }
        overall = ranked_categories(categories, counts)
        self.assertEqual([item["code"] for item in overall], ["A001", "A003", "B002"])
        a_only = ranked_categories(categories, counts, tag="A")
        self.assertEqual([item["code"] for item in a_only], ["A001", "A003"])

    def test_cumulative_counts_never_decrease(self) -> None:
        point_1 = math.degrees(900 / EARTH_RADIUS_METERS)
        point_2 = math.degrees(2500 / EARTH_RADIUS_METERS)
        counts, _, _ = count_points(
            [("A001", point_1, 0.0), ("A001", point_2, 0.0)],
            self.categories,
            0.0,
            0.0,
        )
        self.assertEqual(counts["A001"], [1, 1, 2, 2, 2])

    def test_unmapped_category_is_an_error_not_a_zero(self) -> None:
        with self.assertRaisesRegex(ValueError, "unmapped category code"):
            count_points([("UNKNOWN", 0.0, 0.0)], self.categories, 0.0, 0.0)

    def test_bounding_box_splits_at_the_antimeridian(self) -> None:
        _, _, boxes = bounding_box(0.0, 179.99, 5000.0)
        self.assertEqual(len(boxes), 2)
        self.assertEqual(boxes[0][1], 180.0)
        self.assertEqual(boxes[1][0], -180.0)
        self.assertTrue(is_korea_plausible(126.9780, 37.5665))  # Seoul
        self.assertTrue(is_korea_plausible(126.5312, 33.4996))  # Jeju
        self.assertTrue(is_korea_plausible(131.8656, 37.2426))  # Dokdo
        self.assertFalse(is_korea_plausible(0.0, 0.0))
        self.assertFalse(is_korea_plausible(-122.33, 47.61))
        taxonomy = {
            "taxonomy_version": "example-v1",
            "categories": [
                {"code": "B001", "name": "B", "abc_tag": "B"},
                {"code": "A001", "name": "A", "abc_tag": "A"},
            ],
        }
        reordered = {"categories": list(reversed(taxonomy["categories"])), "taxonomy_version": "other-label"}
        self.assertEqual(canonical_taxonomy_hash(taxonomy), canonical_taxonomy_hash(reordered))
        changed = {**reordered, "categories": [dict(item) for item in reordered["categories"]]}
        changed["categories"][0]["abc_tag"] = "C"
        self.assertNotEqual(canonical_taxonomy_hash(taxonomy), canonical_taxonomy_hash(changed))


if __name__ == "__main__":
    unittest.main()
