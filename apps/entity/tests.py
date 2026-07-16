from django.test import TestCase
from django.core.management import call_command
from apps.entity.models import Entity, Team, Athlete, CanonicalEntity, EntityStats
from apps.entity.utils.matcher import get_or_create_precise_entity
from django.urls import reverse
from rest_framework.test import APIClient

class EntityMatcherTestCase(TestCase):
    def setUp(self):
        self.team_1 = Entity.objects.create(
            name="Real Madrid CF",
            sport="soccer",
            type="team",
            api_source="api_sports",
            external_id="541"
        )
        Team.objects.create(entity=self.team_1)

    def test_exact_name_match(self):
        # Match exact name
        entity = get_or_create_precise_entity("817", "Real Madrid CF", "soccer")
        self.assertEqual(entity.id, self.team_1.id)
        self.assertEqual(entity.external_id, "817")

    def test_fuzzy_name_match_creates_canonical(self):
        # Real Madrid should match Real Madrid CF (>90% similarity)
        entity = get_or_create_precise_entity("817", "Real Madrid", "soccer")
        self.assertEqual(entity.id, self.team_1.id)
        self.assertEqual(entity.external_id, "817")
        self.assertEqual(entity.api_source, "statpal")

        # CanonicalEntity should be created automatically
        canonical = CanonicalEntity.objects.filter(entity=self.team_1).first()
        self.assertIsNotNone(canonical)
        self.assertEqual(canonical.external_ids.get("statpal"), "817")
        self.assertIn("Real Madrid", canonical.name_variations)


class EntityTypeFixCommandTestCase(TestCase):
    def setUp(self):
        # Entity set as team but actually linked to Athlete (type mismatch)
        self.athlete_entity = Entity.objects.create(
            name="David Alaba",
            sport="soccer",
            type="team"  # Wrong type
        )
        self.team_record = Team.objects.create(entity=self.athlete_entity)
        self.athlete_record = Athlete.objects.create(
            entity=self.athlete_entity,
            first_name="David",
            last_name="Alaba"
        )

    def test_dry_run_does_not_modify(self):
        call_command("fix_entity_types", "--dry-run")
        self.athlete_entity.refresh_from_db()
        self.assertEqual(self.athlete_entity.type, "team")
        self.assertTrue(Team.objects.filter(entity=self.athlete_entity).exists())

    def test_command_corrects_type_and_removes_team_record(self):
        call_command("fix_entity_types")
        self.athlete_entity.refresh_from_db()
        self.assertEqual(self.athlete_entity.type, "athlete")
        self.assertFalse(Team.objects.filter(entity=self.athlete_entity).exists())


class StandingsTieBreakerTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.league_entity = Entity.objects.create(
            name="La Liga",
            sport="soccer",
            type="league",
            api_source="statpal",
            external_id="123"
        )
        
        # Real Madrid
        self.rm_entity = Entity.objects.create(
            name="Real Madrid",
            sport="soccer",
            type="team",
            api_source="statpal",
            external_id="817"
        )
        self.rm_team = Team.objects.create(entity=self.rm_entity, league=self.league_entity)
        # 7 points, +5 GD, 8 goals for
        EntityStats.objects.create(
            entity=self.rm_entity,
            season="2025",
            stat_type="season",
            stats_data={
                "rank": 2, # wrong rank in raw data
                "points": 7,
                "goal_diff": 5,
                "goals_for": 8,
                "played": 3
            }
        )

        # Inter
        self.inter_entity = Entity.objects.create(
            name="Inter",
            sport="soccer",
            type="team",
            api_source="statpal",
            external_id="816"
        )
        self.inter_team = Team.objects.create(entity=self.inter_entity, league=self.league_entity)
        # 7 points, +3 GD, 6 goals for
        EntityStats.objects.create(
            entity=self.inter_entity,
            season="2025",
            stat_type="season",
            stats_data={
                "rank": 1, # wrong rank in raw data
                "points": 7,
                "goal_diff": 3,
                "goals_for": 6,
                "played": 3
            }
        )

    def test_standings_ranking_tie_breaker(self):
        url = reverse("team_standings", kwargs={"team_id": self.rm_entity.id}) + "?season=2025"
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        
        standings = resp.json().get("standings", [])
        self.assertEqual(len(standings), 2)
        
        # Real Madrid should rank 1st because of better GD (+5 vs +3)
        self.assertEqual(standings[0]["team_name"], "Real Madrid")
        self.assertEqual(standings[0]["rank"], 1)
        
        # Inter should rank 2nd
        self.assertEqual(standings[1]["team_name"], "Inter")
        self.assertEqual(standings[1]["rank"], 2)
