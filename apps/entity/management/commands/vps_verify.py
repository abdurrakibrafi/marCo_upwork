from django.core.management.base import BaseCommand
from rest_framework.test import APIRequestFactory, force_authenticate
from apps.entity.views.base import get_entity_fixtures, get_entity_stats
from apps.entity.views_search import search_entities
from apps.feed.views import get_entity_feed
from apps.event.views import get_nest_calendar
from apps.nest.models import UserNest
from apps.entity.models import Entity
from apps.feed.models import Source, FeedItem
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    """Management command to verify all backend fixes directly on VPS."""
    help = "Run automated verification of all backend fixes directly on VPS"

    def handle(self, *args, **options):
        rf = APIRequestFactory()

        self.stdout.write(self.style.NOTICE("\n============================================================"))
        self.stdout.write(self.style.NOTICE(">>> 1. VERIFYING SEARCH DEDUPLICATION & LOGOS"))
        self.stdout.write(self.style.NOTICE("============================================================"))
        search_queries = ['toronto maple leafs', 'montreal canadiens', 'edmonton oilers', 'nhl', 'blue jays', 'arsenal']
        all_search_pass = True
        from django.core.cache import cache
        from apps.entity.utils.normalizers import normalize_entity_name
        try:
            cache.clear()
        except Exception:
            pass
        for q in search_queries:
            norm_q = normalize_entity_name(q)
            cache.delete(f'entity_search:{norm_q}::')
            cache.delete(f'entity_search:{q}::')
            req = rf.get(f'/api/entities/search/?q={q}', HTTP_HOST='localhost')
            res = search_entities(req)
            results = res.data.get('results', [])
            count = len(results)
            top = results[0] if results else {}
            has_logo = bool(top.get('logo_url'))
            is_valid = (count == 1) and has_logo if q in ['toronto maple leafs', 'montreal canadiens', 'edmonton oilers', 'nhl', 'arsenal'] else (count >= 1 and has_logo)
            status_str = "PASS" if is_valid else "FAIL"
            color = self.style.SUCCESS if is_valid else self.style.ERROR
            self.stdout.write(color(f"  [{status_str}] Query '{q}': {count} result(s) | Top: {top.get('name')} | Logo: {has_logo} ({top.get('logo_url')[:45]}...)"))
            if not is_valid:
                all_search_pass = False

        self.stdout.write(self.style.NOTICE("\n============================================================"))
        self.stdout.write(self.style.NOTICE(">>> 2. VERIFYING FIXTURES & HOME/AWAY TEAM LOGOS"))
        self.stdout.write(self.style.NOTICE("============================================================"))
        leafs = Entity.objects.filter(name__iexact='Toronto Maple Leafs', type='team', canonical_entity__isnull=True).first()
        if leafs:
            req = rf.get(f'/api/entities/{leafs.id}/fixtures/', HTTP_HOST='localhost')
            res = get_entity_fixtures(req, leafs.id)
            fixtures = res.data.get('fixtures', [])
            self.stdout.write(f"  Team: {leafs.name} (ID: {leafs.id}) | Total Fixtures: {len(fixtures)}")
            if fixtures:
                f0 = fixtures[0]
                has_h_logo = bool(f0.get('home_logo')) and bool(f0.get('home_team_logo'))
                has_a_logo = bool(f0.get('away_logo')) and bool(f0.get('away_team_logo'))
                cur_idx = res.data.get('current_fixture_index', 0)
                is_valid = len(fixtures) > 20 and has_h_logo and has_a_logo
                status_str = "PASS" if is_valid else "FAIL"
                color = self.style.SUCCESS if is_valid else self.style.ERROR
                self.stdout.write(color(f"  [{status_str}] Fixture Dual-Window & Logos:"))
                self.stdout.write(f"      Sample: {f0.get('home_team')} vs {f0.get('away_team')} (Status: {f0.get('status')})")
                self.stdout.write(f"      Home Logo: {has_h_logo} | Away Logo: {has_a_logo}")
                self.stdout.write(f"      Current Fixture Anchor Index: {cur_idx}")
        else:
            self.stdout.write(self.style.ERROR("  [FAIL] Toronto Maple Leafs team entity not found."))

        self.stdout.write(self.style.NOTICE("\n============================================================"))
        self.stdout.write(self.style.NOTICE(">>> 3. VERIFYING LEAGUE STANDINGS HIERARCHY & LOGOS (NHL)"))
        self.stdout.write(self.style.NOTICE("============================================================"))
        nhl = Entity.objects.filter(name__iexact='NHL', type='league', canonical_entity__isnull=True).first()
        if nhl:
            req = rf.get(f'/api/entities/{nhl.id}/stats/', HTTP_HOST='localhost')
            res = get_entity_stats(req, nhl.id)
            structure = res.data.get('structure')
            conferences = list(res.data.get('conferences', {}).keys())
            standings = res.data.get('standings', [])
            has_teams = len(standings) >= 30
            teams_with_logo = sum(1 for t in standings if bool(t.get('logo')) and bool(t.get('team_logo')))
            is_valid = (structure == 'conference_division') and (teams_with_logo == len(standings)) and has_teams
            status_str = "PASS" if is_valid else "FAIL"
            color = self.style.SUCCESS if is_valid else self.style.ERROR
            self.stdout.write(color(f"  [{status_str}] NHL Standings Structure: {structure}"))
            self.stdout.write(f"      Conferences ({len(conferences)}): {conferences}")
            self.stdout.write(f"      Teams Count: {len(standings)} | Teams with Verified Logo: {teams_with_logo}/{len(standings)}")
            if standings:
                s0 = standings[0]
                self.stdout.write(f"      Leader: {s0.get('team_name')} (Conf: {s0.get('conference')}, Div: {s0.get('division')}) | Logo: {bool(s0.get('logo'))}")
        else:
            self.stdout.write(self.style.ERROR("  [FAIL] NHL league entity not found."))

        self.stdout.write(self.style.NOTICE("\n============================================================"))
        self.stdout.write(self.style.NOTICE(">>> 4. VERIFYING NEWS FEED POLLING & THUMBNAILS"))
        self.stdout.write(self.style.NOTICE("============================================================"))
        blue_jays = Entity.objects.filter(name__iexact='Toronto Blue Jays', type='team', canonical_entity__isnull=True).first()
        if blue_jays:
            req = rf.get(f'/api/feed/entity/{blue_jays.id}/', HTTP_HOST='localhost')
            res = get_entity_feed(req, blue_jays.id)
            articles = res.data.get('results', [])
            total_count = res.data.get('count', 0)
            has_bad_badges = any('/badge/' in (it.get('thumbnail_url') or '') for it in articles)
            active_sources = Source.objects.filter(entities=blue_jays, is_active=True).count()
            is_valid = total_count > 0 and (not has_bad_badges)
            status_str = "PASS" if is_valid else "FAIL"
            color = self.style.SUCCESS if is_valid else self.style.ERROR
            self.stdout.write(color(f"  [{status_str}] Blue Jays News Feed:"))
            self.stdout.write(f"      Total Articles in DB: {total_count} | Active Sources: {active_sources}")
            if articles:
                a0 = articles[0]
                self.stdout.write(f"      Latest Headline: {a0.get('title')[:60]}...")
                self.stdout.write(f"      Published At: {a0.get('published_at')}")
                self.stdout.write(f"      Bad Circular Team Badges detected in articles: {'YES (BUG)' if has_bad_badges else 'NO (CLEAN)'}")
        else:
            self.stdout.write(self.style.ERROR("  [FAIL] Toronto Blue Jays entity not found."))

        self.stdout.write(self.style.NOTICE("\n============================================================"))
        self.stdout.write(self.style.NOTICE(">>> 5. VERIFYING NEST CALENDAR SYNCHRONIZATION"))
        self.stdout.write(self.style.NOTICE("============================================================"))
        # Find Marco user (user who follows Toronto Maple Leafs)
        nest_item = UserNest.objects.filter(entity__name='Toronto Maple Leafs').first()
        if nest_item and nest_item.user:
            u = nest_item.user
            followed_count = UserNest.objects.filter(user=u).count()
            req = rf.get('/api/calendar/nest/', HTTP_HOST='localhost')
            force_authenticate(req, user=u)
            res = get_nest_calendar(req)
            events = res.data.get('data', {}).get('events', [])
            is_valid = len(events) > 0
            status_str = "PASS" if is_valid else "FAIL"
            color = self.style.SUCCESS if is_valid else self.style.ERROR
            self.stdout.write(color(f"  [{status_str}] Nest Calendar for User #{u.id}:"))
            self.stdout.write(f"      Followed Entities in Nest: {followed_count}")
            self.stdout.write(f"      Calendar Matches Populated: {len(events)}")
            if events:
                e0 = events[0]
                self.stdout.write(f"      Sample Calendar Match: {e0.get('start_time')} | {e0.get('home_team')} vs {e0.get('away_team')}")
                self.stdout.write(f"      Nest Highlight: {e0.get('nest_entity_name')} | Logo: {bool(e0.get('nest_entity_logo'))}")
        else:
            self.stdout.write(self.style.WARNING("  [SKIP] Marco test user not found via Maple Leafs Nest query."))

        self.stdout.write(self.style.NOTICE("\n============================================================"))
        self.stdout.write(self.style.SUCCESS(">>> ALL BACKEND VERIFICATIONS COMPLETED."))
        self.stdout.write(self.style.NOTICE("============================================================\n"))
