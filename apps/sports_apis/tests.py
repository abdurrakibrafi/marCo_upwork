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

    @patch('requests.get')
    def test_tennis_backfill(self, mock_get):
        class MockResponse:
            def __init__(self, text, status_code):
                self.text = text
                self.status_code = status_code
            def json(self):
                return [
                    "test",
                    ["test"],
                    ["description"],
                    ["https://en.wikipedia.org/wiki/Test"]
                ]

        html_content = """
        <table class="wikitable">
            <tr><th>No.</th><th>Player</th><th>Points</th><th>Move</th></tr>
            <tr><td>1</td><td><span class="flagicon"><img alt="ITA" src="flag.png"></span><a href="/wiki/Jannik_Sinner">Jannik Sinner</a> (ITA)</td><td>13,450</td><td></td></tr>
        </table>
        <table class="wikitable"><tr><th>Header</th></tr></table>
        <table class="wikitable"><tr><th>Header</th></tr></table>
        <table class="wikitable"><tr><th>Header</th></tr></table>
        <table class="wikitable">
            <tr><th>No.</th><th>Player</th><th>Points</th><th>Move</th></tr>
            <tr><td>1</td><td><span class="flagicon"><img alt="USA" src="flag.png"></span><a href="/wiki/Coco_Gauff">Coco Gauff</a></td><td>6,000</td><td></td></tr>
        </table>
        """
        mock_get.return_value = MockResponse(html_content, 200)

        # Clear existing athletes
        Athlete.objects.all().delete()
        Entity.objects.filter(sport='tennis').delete()

        # Run command
        call_command('backfill_tennis_players')

        # Check seeded tennis athletes
        self.assertEqual(Athlete.objects.filter(entity__sport='tennis').count(), 2)
        sinner = Athlete.objects.filter(first_name='Jannik').first()
        self.assertIsNotNone(sinner)
        self.assertEqual(sinner.last_name, 'Sinner')
        self.assertEqual(sinner.nationality, 'ITA')

    @patch('requests.get')
    def test_golf_backfill(self, mock_get):
        class MockResponse:
            def __init__(self, text, status_code):
                self.text = text
                self.status_code = status_code

        html_content = """
        <table class="wikitable">
            <tr><th>Rank</th><th>Change</th><th>Player</th></tr>
            <tr><td>1</td><td></td><td><span class="flagicon"><span class="mw-image-border"><a href="/wiki/Australia"><img alt="Australia" src="flag.png"></a></span></span><a href="/wiki/Jason_Day">Jason Day</a></td></tr>
        </table>
        """
        mock_get.return_value = MockResponse(html_content, 200)

        Athlete.objects.all().delete()
        Entity.objects.filter(sport='golf').delete()

        call_command('backfill_golf_players')

        self.assertEqual(Athlete.objects.filter(entity__sport='golf').count(), 1)
        jason = Athlete.objects.filter(first_name='Jason').first()
        self.assertIsNotNone(jason)
        self.assertEqual(jason.last_name, 'Day')
        self.assertEqual(jason.nationality, 'Australia')

    @patch('requests.get')
    def test_team_sports_backfill(self, mock_get):
        # Create dummy handball and volleyball teams
        handball_team = Entity.objects.create(
            type='team',
            name='FC Barcelona',
            sport='handball',
            has_api_data=True
        )
        volleyball_team = Entity.objects.create(
            type='team',
            name='Zenit Kazan',
            sport='volleyball',
            has_api_data=True
        )

        def mock_requests_get(url, *args, **kwargs):
            class MockResponse:
                def __init__(self, data, status_code, is_json=False):
                    self.data = data
                    self.status_code = status_code
                    self.is_json = is_json
                def json(self):
                    return self.data
                @property
                def text(self):
                    return self.data

            if "action=opensearch" in url:
                return MockResponse(["search", ["Barcelona"], ["desc"], ["https://en.wikipedia.org/wiki/FC_Barcelona_Handbol"]], 200, is_json=True)
            elif "FC_Barcelona_Handbol" in url or "Zenit" in url:
                html = """
                <table>
                    <tr><th>No.</th><th>Player</th><th>Position</th><th>Nat.</th></tr>
                    <tr><td>10</td><td><span class="flagicon"><img alt="ESP"></span><a href="/wiki/Dika_Mem">Dika Mem</a></td><td>Right Back</td><td>ESP</td></tr>
                </table>
                """
                return MockResponse(html, 200)
            return MockResponse("Not Found", 404)

        mock_get.side_effect = mock_requests_get

        Athlete.objects.all().delete()

        call_command('backfill_handball_players')
        self.assertEqual(Athlete.objects.filter(entity__sport='handball').count(), 1)
        mem = Athlete.objects.filter(first_name='Dika').first()
        self.assertIsNotNone(mem)
        self.assertEqual(mem.last_name, 'Mem')
        self.assertEqual(mem.jersey_number, 10)
        self.assertEqual(mem.current_team, handball_team)

