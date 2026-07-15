from django.test import TestCase
from django.core.management import call_command
from unittest.mock import patch
from apps.entity.models import Entity, Athlete

class BackfillRostersTestCase(TestCase):
    def setUp(self):
        # Create dummy baseball team
        self.baseball_team = Entity.objects.create(
            type='team',
            name='Toronto Blue Jays',
            sport='baseball',
            api_source='statpal',
            external_id='2504',
            has_api_data=True
        )
        # Create dummy hockey team
        self.hockey_team = Entity.objects.create(
            type='team',
            name='Toronto Maple Leafs',
            sport='hockey',
            api_source='statpal',
            external_id='2510',
            has_api_data=True
        )

    @patch('requests.get')
    def test_backfill_command(self, mock_get):
        # Set up mock response behavior
        def mock_requests_get(url, *args, **kwargs):
            class MockResponse:
                def __init__(self, json_data, status_code):
                    self.json_data = json_data
                    self.status_code = status_code
                def json(self):
                    return self.json_data

            if "teams?sportId=1" in url:
                return MockResponse({
                    "teams": [
                        {
                            "id": 141,
                            "name": "Toronto Blue Jays",
                            "abbreviation": "TOR"
                        }
                    ]
                }, 200)
            elif "standings/now" in url:
                return MockResponse({
                    "standings": [
                        {
                            "teamAbbrev": {"default": "TOR"},
                            "teamName": {"default": "Toronto Maple Leafs"},
                            "teamCommonName": {"default": "Maple Leafs"}
                        }
                    ]
                }, 200)
            elif "teams/141/roster" in url:
                return MockResponse({
                    "roster": [
                        {
                            "person": {
                                "id": 671936,
                                "fullName": "Adam Macko",
                                "firstName": "Adam",
                                "lastName": "Macko",
                                "birthDate": "2000-12-30",
                                "birthCountry": "Slovakia",
                                "height": "6' 0\"",
                                "weight": 170,
                                "primaryPosition": {
                                    "abbreviation": "P"
                                }
                            },
                            "jerseyNumber": "64"
                        }
                    ]
                }, 200)
            elif "roster/TOR/current" in url:
                return MockResponse({
                    "forwards": [
                        {
                            "id": 8476927,
                            "firstName": {"default": "Teddy"},
                            "lastName": {"default": "Blueger"},
                            "positionCode": "C",
                            "jerseyNumber": "57",
                            "heightInCentimeters": 183,
                            "weightInKilograms": 84,
                            "birthDate": "1994-08-15",
                            "birthCountry": "LVA",
                            "headshot": "https://assets.nhle.com/mugs/nhl/TOR/8476927.png"
                        }
                    ],
                    "defensemen": [],
                    "goalies": []
                }, 200)
            return MockResponse({}, 404)

        mock_get.side_effect = mock_requests_get

        # Run command in dry-run mode
        call_command('backfill_mlb_nhl_rosters', dry_run=True)
        # Verify no players created in DB in dry-run
        self.assertEqual(Athlete.objects.count(), 0)

        # Run command to actually write data
        call_command('backfill_mlb_nhl_rosters')

        # Verify players created
        self.assertEqual(Athlete.objects.count(), 2)

        # Check baseball athlete details
        baseball_athlete = Athlete.objects.filter(entity__sport='baseball').first()
        self.assertIsNotNone(baseball_athlete)
        self.assertEqual(baseball_athlete.first_name, 'Adam')
        self.assertEqual(baseball_athlete.last_name, 'Macko')
        self.assertEqual(baseball_athlete.jersey_number, 64)
        self.assertEqual(baseball_athlete.position, 'P')
        self.assertEqual(baseball_athlete.nationality, 'Slovakia')
        self.assertEqual(baseball_athlete.height_cm, 182)  # 6 feet in cm is 182.88 -> parsed as 182
        self.assertEqual(baseball_athlete.weight_kg, 77)   # 170 lbs in kg is 77.11 -> parsed as 77
        self.assertEqual(baseball_athlete.current_team, self.baseball_team)

        # Check hockey athlete details
        hockey_athlete = Athlete.objects.filter(entity__sport='hockey').first()
        self.assertIsNotNone(hockey_athlete)
        self.assertEqual(hockey_athlete.first_name, 'Teddy')
        self.assertEqual(hockey_athlete.last_name, 'Blueger')
        self.assertEqual(hockey_athlete.jersey_number, 57)
        self.assertEqual(hockey_athlete.position, 'C')
        self.assertEqual(hockey_athlete.nationality, 'LVA')
        self.assertEqual(hockey_athlete.height_cm, 183)
        self.assertEqual(hockey_athlete.weight_kg, 84)
        self.assertEqual(hockey_athlete.current_team, self.hockey_team)
