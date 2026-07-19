from django.utils import timezone
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from apps.entity.models import Entity
from apps.feed.models import FeedItem, Source
from apps.nest.models import UserNest

User = get_user_model()

class FeedTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="testuser", email="test@example.com", password="password")
        self.client.force_authenticate(user=self.user)
        
        # Create entities
        self.entity1 = Entity.objects.create(name="Team A", sport="soccer", type="team")
        self.entity2 = Entity.objects.create(name="Team B", sport="soccer", type="team")
        
        # Link both entities to UserNest
        UserNest.objects.create(user=self.user, entity=self.entity1)
        UserNest.objects.create(user=self.user, entity=self.entity2)
        
        # Create a Source
        self.source = Source.objects.create(name="Test Source", domain="test.com")
        
        # Create a FeedItem linked to BOTH entities
        self.feed_item = FeedItem.objects.create(
            title="Important Soccer Match",
            summary="Match summary",
            url="https://test.com/match",
            source=self.source,
            published_at=timezone.now()
        )
        self.feed_item.entities.add(self.entity1, self.entity2)

    def test_nest_feed_no_duplicates(self):
        # Even though the feed item is associated with 2 entities in the user's nest,
        # it should only appear once in the nest feed response because of distinct()
        url = "/api/feed/nest/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        results = response.data.get("results", [])
        
        # Extract IDs
        ids = [item["id"] for item in results]
        self.assertEqual(len(ids), 1)
        self.assertEqual(ids[0], self.feed_item.id)

    def test_national_team_sports_context_filtering(self):
        from apps.feed.tasks import _entity_matches_text
        
        # Create a national team entity
        brazil = Entity.objects.create(name="Brazil", sport="soccer", type="team")
        
        # 1. Non-sports news with "Brazil" should fail
        non_sports_title = "Supreme Court of Brazil orders investigation into Elon Musk"
        self.assertFalse(_entity_matches_text(brazil, non_sports_title))
        
        # 2. Sports news with "Brazil" should pass
        sports_title = "Brazil striker Neymar scores brilliant goal in Copa America"
        self.assertTrue(_entity_matches_text(brazil, sports_title))

    def test_ensure_entity_has_rss_source_scoping_and_migration(self):
        from apps.feed.tasks import ensure_entity_has_rss_source
        
        # Create a national team entity
        brazil = Entity.objects.create(name="Brazil", sport="soccer", type="team")
        
        # Create an old unscoped active source for Brazil
        old_url = "https://news.google.com/rss/search?q=Brazil&hl=en&gl=US&ceid=US:en"
        old_source = Source.objects.create(
            name="Google News - Brazil",
            rss_url=old_url,
            is_active=True
        )
        old_source.entities.add(brazil)
        
        # Trigger the RSS source task
        ensure_entity_has_rss_source(brazil.id)
        
        # The old source should be deactivated
        old_source.refresh_from_db()
        self.assertFalse(old_source.is_active)
        
        # A new scoped source should be created and active
        scoped_source = Source.objects.get(
            rss_url="https://news.google.com/rss/search?q=%22Brazil%22%20AND%20%28soccer%20OR%20football%29&hl=en&gl=US&ceid=US:en"
        )
        self.assertTrue(scoped_source.is_active)
        self.assertIn(brazil, scoped_source.entities.all())

