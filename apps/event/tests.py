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
