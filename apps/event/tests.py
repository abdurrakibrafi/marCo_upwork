from django.test import TestCase
from apps.event.tasks import _map_status, _extract_minute

class StatusMappingTestCase(TestCase):
    def test_extract_minute(self):
        self.assertEqual(_extract_minute("73"), 73)
        self.assertEqual(_extract_minute("pen miss 73"), 73)
        self.assertEqual(_extract_minute("90+3"), 90)
        self.assertEqual(_extract_minute("45+2"), 45)
        self.assertEqual(_extract_minute("something"), 0)
        self.assertEqual(_extract_minute(""), 0)
        self.assertEqual(_extract_minute(None), 0)

    def test_map_status_soccer(self):
        self.assertEqual(_map_status("76", sport="soccer"), "live")
        self.assertEqual(_map_status("90+3", sport="soccer"), "live")
        self.assertEqual(_map_status("Cancl.", sport="soccer"), "cancelled")
        self.assertEqual(_map_status("FT", sport="soccer"), "completed")

    def test_map_status_tennis(self):
        # A bare number like '1' for tennis should not map to live if metadata scores are empty
        metadata_empty = {
            "player": [
                {"s1": "", "totalscore": ""},
                {"s1": "", "totalscore": ""}
            ]
        }
        self.assertEqual(_map_status("1", sport="tennis", metadata=metadata_empty), "upcoming")

        # But if scores are populated, it is live
        metadata_live = {
            "player": [
                {"s1": "3", "totalscore": ""},
                {"s1": "2", "totalscore": ""}
            ]
        }
        self.assertEqual(_map_status("1", sport="tennis", metadata=metadata_live), "live")
        self.assertEqual(_map_status("Retired", sport="tennis", metadata=metadata_live), "completed")


class EventStatisticsNormalizationTestCase(TestCase):
    """Verify that event statistics normalization includes shot on goal, shot off goal, block shots, and pass accuracy."""

    def test_normalize_statpal_nested_stats(self):
        from apps.event.utils_stats import normalize_event_stats

        raw_statpal = {
            "shots": {"ongoal": 6, "offgoal": 4, "total": 14, "blocked": 4},
            "passes": {"total": 450, "accurate": 380, "percentage": "84%"},
            "possession_percent": {"total": 58},
            "fouls": {"total": 8},
            "corners": {"total": 5}
        }

        normalized = normalize_event_stats(raw_statpal)

        self.assertEqual(normalized["shot_on_goal"], 6)
        self.assertEqual(normalized["shot_off_goal"], 4)
        self.assertEqual(normalized["block_shots"], 4)
        self.assertEqual(normalized["pass_accuracy"], "84%")
        self.assertEqual(normalized["fouls"], 8)
        self.assertEqual(normalized["corners"], 5)
        self.assertEqual(normalized["possession_percent"], "58")

    def test_normalize_flat_stats(self):
        from apps.event.utils_stats import normalize_event_stats

        raw_flat = {
            "shots_on_goal": 7,
            "shots_off_goal": 3,
            "blocked_shots": 2,
            "pass_accuracy": "88%"
        }

        normalized = normalize_event_stats(raw_flat)

        self.assertEqual(normalized["shot_on_goal"], 7)
        self.assertEqual(normalized["shot_off_goal"], 3)
        self.assertEqual(normalized["block_shots"], 2)
        self.assertEqual(normalized["pass_accuracy"], "88%")

