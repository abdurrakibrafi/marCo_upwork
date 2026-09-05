import json
import sys
import types

# Provide stub for daphne to allow channels.testing.WebsocketCommunicator import without daphne installed
if 'daphne' not in sys.modules:
    daphne_mod = types.ModuleType('daphne')
    daphne_testing_mod = types.ModuleType('daphne.testing')
    daphne_testing_mod.DaphneProcess = None
    daphne_mod.testing = daphne_testing_mod
    sys.modules['daphne'] = daphne_mod
    sys.modules['daphne.testing'] = daphne_testing_mod

from django.test import TransactionTestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase, APIClient
from channels.testing.websocket import WebsocketCommunicator
from apps.score.models import LiveScore
from config.asgi import application

User = get_user_model()


class LiveScoreDetailAPITestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='testuser@example.com',
            password='testpassword123',
            first_name='Test',
            last_name='User'
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.live_score = LiveScore.objects.create(
            sport='soccer',
            external_id='test_live_123',
            home_team='Arsenal',
            away_team='Chelsea',
            home_logo='https://example.com/arsenal.png',
            away_logo='https://example.com/chelsea.png',
            home_score=2,
            away_score=1,
            status='live',
            status_detail='75',
            start_time=timezone.now(),
            raw_data={
                'team_stats': {
                    'home': {'shots': {'total': 12, 'ongoal': 5}},
                    'away': {'shots': {'total': 8, 'ongoal': 3}}
                },
                'event_summary': {
                    'home': {
                        'goals': {
                            'event': [{'minute': '25', 'player_id': '10', 'player_name': 'Saka'}]
                        }
                    }
                },
                'lineups': {
                    'home': {'formation': '4-3-3', 'startXI': [], 'substitutes': []},
                    'away': {'formation': '4-2-3-1', 'startXI': [], 'substitutes': []}
                }
            }
        )

    def test_live_score_detail_api_success(self):
        url = f'/api/scores/live/detail/{self.live_score.id}/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.get('success'))
        data = response.data.get('data')
        self.assertIsNotNone(data)
        self.assertEqual(data['id'], self.live_score.id)
        self.assertEqual(data['sport'], 'soccer')
        self.assertEqual(data['home_team'], 'Arsenal')
        self.assertEqual(data['away_team'], 'Chelsea')
        self.assertEqual(data['home_score'], 2)
        self.assertEqual(data['away_score'], 1)
        self.assertIn('scorecard', data)
        self.assertIn('lineups', data)
        self.assertIn('events', data)
        self.assertIn('statistics', data)

    def test_live_score_detail_api_not_found(self):
        url = '/api/scores/live/detail/999999/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.data.get('success'))


from unittest.mock import patch

class LiveScoreDetailWebSocketTestCase(TransactionTestCase):
    def setUp(self):
        self.statpal_patcher = patch('apps.sports_apis.services.statpal.statpal_service.get_cricket_live', return_value={'success': False})
        self.statpal_patcher.start()
        self.user = User.objects.create_user(
            email='wsuser@example.com',
            password='testpassword123',
            first_name='WS',
            last_name='User'
        )
        self.live_score = LiveScore.objects.create(
            sport='cricket',
            external_id='test_cricket_456',
            home_team='England',
            away_team='Australia',
            home_logo='https://example.com/england.png',
            away_logo='https://example.com/australia.png',
            home_score=150,
            away_score=120,
            status='live',
            status_detail='In Progress',
            start_time=timezone.now(),
            raw_data={
                'inning': [
                    {
                        'name': 'England 1st Innings',
                        'batsmanstats': {
                            'player': [{'batsman': 'Root', 'r': '50', 'b': '60', 's4': '5', 's6': '1', 'sr': '83.3'}]
                        },
                        'bowlers': {
                            'player': [{'bowler': 'Starc', 'o': '10', 'r': '40', 'w': '2', 'er': '4.0'}]
                        }
                    }
                ]
            }
        )

    def tearDown(self):
        self.statpal_patcher.stop()

    async def test_websocket_connect_and_receive_detail(self):
        communicator = WebsocketCommunicator(
            application,
            f'/ws/scores/live/detail/{self.live_score.id}/'
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        # Receive snapshot on connect
        response_text = await communicator.receive_from()
        response = json.loads(response_text)
        self.assertTrue(response.get('success'))
        self.assertEqual(response.get('type'), 'live_score_detail')
        data = response.get('data')
        self.assertIsNotNone(data)
        self.assertEqual(data['id'], self.live_score.id)
        self.assertEqual(data['sport'], 'cricket')
        self.assertEqual(data['home_team'], 'England')
        self.assertEqual(data['away_team'], 'Australia')
        self.assertIn('England 1st Innings', data.get('scorecard', {}))

        # Test ping/pong
        await communicator.send_json_to({'type': 'ping'})
        pong_text = await communicator.receive_from()
        pong = json.loads(pong_text)
        self.assertEqual(pong.get('type'), 'pong')

        await communicator.disconnect()

    async def test_websocket_subscribe_action(self):
        communicator = WebsocketCommunicator(
            application,
            '/ws/scores/live/detail/'
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        # Subscribe dynamically via message
        await communicator.send_json_to({
            'action': 'subscribe',
            'score_id': self.live_score.id
        })

        response_text = await communicator.receive_from()
        response = json.loads(response_text)
        self.assertTrue(response.get('success'))
        self.assertEqual(response['data']['id'], self.live_score.id)

        await communicator.disconnect()
