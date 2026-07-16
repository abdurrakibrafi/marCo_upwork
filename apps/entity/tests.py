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
        # Case A: Entity set as team but actually linked to Athlete (type mismatch)
        self.athlete_entity = Entity.objects.create(
            name="David Alaba",
            sport="soccer",
            type="team"  # Wrong type
        )
        self.team_record_a = Team.objects.create(entity=self.athlete_entity)
        self.athlete_record_a = Athlete.objects.create(
            entity=self.athlete_entity,
            first_name="David",
            last_name="Alaba"
        )

        # Case B: Real team with collided Athlete record (names differ)
        self.team_entity = Entity.objects.create(
            name="Boston Celtics",
            sport="basketball",
            type="team"  # Correct type
        )
        self.team_record_b = Team.objects.create(entity=self.team_entity)
        self.athlete_record_b = Athlete.objects.create(
            entity=self.team_entity,  # Collided link
            first_name="Jayson",
            last_name="Tatum"
        )

    def test_dry_run_does_not_modify(self):
        call_command("fix_entity_types", "--dry-run")
        self.athlete_entity.refresh_from_db()
        self.team_entity.refresh_from_db()
        
        self.assertEqual(self.athlete_entity.type, "team")
        self.assertEqual(self.team_entity.type, "team")
        self.assertTrue(Team.objects.filter(entity=self.athlete_entity).exists())
        self.assertTrue(Athlete.objects.filter(entity=self.team_entity).exists())

    def test_command_execution(self):
        call_command("fix_entity_types")
        self.athlete_entity.refresh_from_db()
        self.team_entity.refresh_from_db()

        # Case A corrected
        self.assertEqual(self.athlete_entity.type, "athlete")
        self.assertFalse(Team.objects.filter(entity=self.athlete_entity).exists())

        # Case B cleaned (team type kept, incorrect athlete record deleted)
        self.assertEqual(self.team_entity.type, "team")
        self.assertTrue(Team.objects.filter(entity=self.team_entity).exists())
        self.assertFalse(Athlete.objects.filter(entity=self.team_entity).exists())


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


class LogoRestoreCommandTestCase(TestCase):
    def setUp(self):
        # Target Entity: basketball team with a broken StatPal logo
        self.target_team = Entity.objects.create(
            name="Los Angeles Lakers",
            sport="basketball",
            type="team",
            logo_url="https://statpal.io/images/bad-url.png",
            api_source="statpal",
            external_id="123"
        )
        # Source Entity: duplicate lakers team with correct logo from api_sports
        self.source_team = Entity.objects.create(
            name="Lakers", # different name for fuzzy matching testing
            sport="basketball",
            type="team",
            logo_url="https://images.nba.com/lakers-logo.png",
            api_source="api_sports",
            external_id="456"
        )

    def test_dry_run_does_not_modify(self):
        call_command("restore_logos_from_duplicates", "--dry-run")
        self.target_team.refresh_from_db()
        self.assertEqual(self.target_team.logo_url, "https://statpal.io/images/bad-url.png")

    def test_command_restores_logo(self):
        call_command("restore_logos_from_duplicates")
        self.target_team.refresh_from_db()
        self.assertEqual(self.target_team.logo_url, "https://images.nba.com/lakers-logo.png")
