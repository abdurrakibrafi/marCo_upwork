from django.utils import timezone
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from apps.entity.models import Entity
from apps.feed.models import FeedItem, Source, Like, Bookmark
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
        import hashlib
        self.feed_item = FeedItem.objects.create(
            title="Important Soccer Match",
            summary="Match summary",
            url="https://test.com/match",
            url_hash=hashlib.md5("https://test.com/match".encode()).hexdigest(),
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

    def test_nest_feed_drf_pagination_format(self):
        url = "/api/feed/nest/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("count", response.data)
        self.assertIn("next", response.data)
        self.assertIn("previous", response.data)
        self.assertIn("results", response.data)
        self.assertEqual(response.data["count"], 1)
        self.assertIsNone(response.data["next"])
        self.assertIsNone(response.data["previous"])
        self.assertIsInstance(response.data["results"], list)

    def test_nest_feed_type_parameter_support(self):
        # Test that ?type=teams correctly filters feed items
        url = "/api/feed/nest/?type=teams"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        results = response.data.get("results", [])
        self.assertEqual(len(results), 1)

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
        scoped_source = Source.objects.filter(entities=brazil, is_active=True).exclude(id=old_source.id).first()
        self.assertIsNotNone(scoped_source)
        self.assertTrue(scoped_source.is_active)
        self.assertIn(brazil, scoped_source.entities.all())
        self.assertIn("Brazil", scoped_source.rss_url)

    def test_google_news_url_resolution(self):
        import hashlib
        from apps.feed.utils_url import resolve_real_article_url
        from apps.feed.serializers import FeedItemCompactSerializer

        # Test non-google URL unchanged
        self.assertEqual(resolve_real_article_url("https://espn.com/article"), "https://espn.com/article")

        # Test serializer dynamically resolves URL for Google News feed item
        target_url = "https://test.com/direct-link"
        item = FeedItem.objects.create(
            title="Decoded News Article",
            summary="Article summary",
            url=target_url,
            url_hash=hashlib.md5(target_url.encode()).hexdigest(),
            source=self.source,
            published_at=timezone.now()
        )
        item.entities.add(self.entity1)

        serializer = FeedItemCompactSerializer(item)
        self.assertEqual(serializer.data["url"], "https://test.com/direct-link")

    def test_nest_feed_sort_by_likes(self):
        import hashlib
        # Create a second feed item with more likes
        item2 = FeedItem.objects.create(
            title="Second Match",
            summary="Match summary 2",
            url="https://test.com/match2",
            url_hash=hashlib.md5("https://test.com/match2".encode()).hexdigest(),
            source=self.source,
            published_at=timezone.now()
        )
        item2.entities.add(self.entity1)

        # Add 2 likes to item2
        user2 = User.objects.create_user(username="user2", email="user2@example.com", password="password")
        Like.objects.create(user=self.user, feed_item=item2)
        Like.objects.create(user=user2, feed_item=item2)

        # Query GET /api/feed/nest/?sort=least
        response = self.client.get("/api/feed/nest/?sort=least")
        self.assertEqual(response.status_code, 200)
        results = response.data.get("results", [])
        self.assertGreaterEqual(len(results), 2)
        # item2 should be first because it has 2 likes vs 0
        self.assertEqual(results[0]["id"], item2.id)
        self.assertEqual(results[0]["like_count"], 2)

    def test_url_tracking_parameter_clean(self):
        from apps.feed.utils_url import clean_tracking_parameters
        raw_url = "https://www.bbc.com/sport/football/123456?utm_source=rss&utm_medium=feed&fbclid=XYZ123&id=99"
        cleaned = clean_tracking_parameters(raw_url)
        self.assertNotIn("utm_source", cleaned)
        self.assertNotIn("utm_medium", cleaned)
        self.assertNotIn("fbclid", cleaned)
        self.assertIn("id=99", cleaned)
        self.assertTrue(cleaned.startswith("https://www.bbc.com/sport/football/123456"))
    def test_bookmark_api_endpoints(self):
        # 1. Toggle bookmark ON
        res = self.client.post("/api/feed/bookmark/", {"feed_item_id": self.feed_item.id}, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data, {"bookmarked": True})
        self.assertTrue(Bookmark.objects.filter(user=self.user, feed_item=self.feed_item).exists())

        # 2. Get bookmarks list
        res_list = self.client.get("/api/feed/bookmarks/")
        self.assertEqual(res_list.status_code, 200)
        self.assertEqual(res_list.data["count"], 1)
        self.assertEqual(res_list.data["results"][0]["feed_item"]["id"], self.feed_item.id)

        # 3. Toggle bookmark OFF
        res_off = self.client.post("/api/feed/bookmark/", {"feed_item_id": self.feed_item.id}, format="json")
        self.assertEqual(res_off.status_code, 200)
        self.assertEqual(res_off.data, {"bookmarked": False})
        self.assertFalse(Bookmark.objects.filter(user=self.user, feed_item=self.feed_item).exists())

        # 4. Remove bookmark via DELETE
        Bookmark.objects.create(user=self.user, feed_item=self.feed_item)
        res_del = self.client.delete(f"/api/feed/bookmarks/{self.feed_item.id}/")
        self.assertEqual(res_del.status_code, 200)
        self.assertEqual(res_del.data, {"success": True})
        self.assertFalse(Bookmark.objects.filter(user=self.user, feed_item=self.feed_item).exists())

        # 5. Remove bookmark not found returns 404
        res_del_404 = self.client.delete(f"/api/feed/bookmarks/{self.feed_item.id}/")
        self.assertEqual(res_del_404.status_code, 404)

    def test_like_and_like_count_api_endpoints(self):
        # 1. Toggle like ON
        res = self.client.post("/api/feed/like/", {"feed_item_id": self.feed_item.id}, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["liked"], True)
        self.assertEqual(res.data["like_count"], 1)
        self.assertTrue(Like.objects.filter(user=self.user, feed_item=self.feed_item).exists())

        # 2. Get likes list
        res_likes = self.client.get("/api/feed/likes/")
        self.assertEqual(res_likes.status_code, 200)
        self.assertEqual(res_likes.data["count"], 1)
        self.assertEqual(res_likes.data["results"][0]["feed_item"]["id"], self.feed_item.id)

        # 3. Another user also likes the same item
        user2 = User.objects.create_user(username="user2", email="user2@example.com", password="password")
        Like.objects.create(user=user2, feed_item=self.feed_item)

        # 4. Toggle like OFF for user 1
        res_off = self.client.post("/api/feed/like/", {"feed_item_id": self.feed_item.id}, format="json")
        self.assertEqual(res_off.status_code, 200)
        self.assertEqual(res_off.data["liked"], False)
        self.assertEqual(res_off.data["like_count"], 1)  # remaining like from user2

        # 5. Check feed item reflection (is_liked, is_bookmarked, like_count)
        Bookmark.objects.create(user=self.user, feed_item=self.feed_item)
        feed_res = self.client.get("/api/feed/nest/")
        self.assertEqual(feed_res.status_code, 200)
        item_data = next(it for it in feed_res.data["results"] if it["id"] == self.feed_item.id)
        self.assertEqual(item_data["is_liked"], False)
        self.assertEqual(item_data["is_bookmarked"], True)
        self.assertEqual(item_data["like_count"], 1)

        # 6. Remove like via DELETE for user2
        self.client.force_authenticate(user=user2)
        res_del = self.client.delete(f"/api/feed/likes/{self.feed_item.id}/")
        self.assertEqual(res_del.status_code, 200)
        self.assertEqual(res_del.data, {"success": True})
        self.assertEqual(Like.objects.filter(feed_item=self.feed_item).count(), 0)

        # 7. Delete non-existent like returns 404
        res_del_404 = self.client.delete(f"/api/feed/likes/{self.feed_item.id}/")
        self.assertEqual(res_del_404.status_code, 404)


from unittest.mock import patch

class PollingEntityLinkingTestCase(TestCase):
    def setUp(self):
        self.canonical_team = Entity.objects.create(name="Arsenal", sport="soccer", type="team")
        self.duplicate_team = Entity.objects.create(
            name="Arsenal FC", sport="soccer", type="team", canonical_entity=self.canonical_team
        )
        self.unrelated_team = Entity.objects.create(name="Los Angeles Lakers", sport="basketball", type="team")
        self.mufc_team = Entity.objects.create(name="Manchester United", sport="soccer", type="team")
        self.tottenham = Entity.objects.create(name="Tottenham Hotspur", sport="soccer", type="team")

        self.source = Source.objects.create(
            name="Multi Sports Source", domain="multisports.com", rss_url="https://multisports.com/rss"
        )
        self.source.entities.add(self.canonical_team, self.unrelated_team, self.mufc_team, self.tottenham)

    @patch("apps.feed.tasks.polling.rss_polling_service.poll_feed")
    def test_unmatched_article_not_linked(self, mock_poll):
        mock_poll.return_value = {
            "success": True,
            "entries": [{
                "url": "https://multisports.com/golf-tourney-123",
                "title": "Golf Tournament Update in Miami",
                "summary": "Fading light forces suspension of golf championship.",
                "published_at": timezone.now(),
            }]
        }
        from apps.feed.tasks.polling import poll_single_source
        poll_single_source(self.source.id)

        # Unmatched article is skipped and NOT linked to candidate entities
        self.assertFalse(FeedItem.objects.filter(url="https://multisports.com/golf-tourney-123").exists())

    @patch("apps.feed.tasks.polling.rss_polling_service.poll_feed")
    def test_matched_article_links_only_matched_and_canonical_duplicates(self, mock_poll):
        mock_poll.return_value = {
            "success": True,
            "entries": [{
                "url": "https://multisports.com/arsenal-match-456",
                "title": "Arsenal secures dramatic victory in London derby",
                "summary": "Mikel Arteta praises the team performance.",
                "published_at": timezone.now(),
            }]
        }
        from apps.feed.tasks.polling import poll_single_source
        poll_single_source(self.source.id)

        item = FeedItem.objects.filter(url="https://multisports.com/arsenal-match-456").first()
        self.assertIsNotNone(item)
        linked_ids = set(item.entities.values_list("id", flat=True))

        # Must link matched team and its canonical duplicate
        self.assertIn(self.canonical_team.id, linked_ids)
        self.assertIn(self.duplicate_team.id, linked_ids)

        # Must NOT link unrelated candidates from the source bundle
        self.assertNotIn(self.unrelated_team.id, linked_ids)
        self.assertNotIn(self.mufc_team.id, linked_ids)

    @patch("apps.feed.tasks.polling.rss_polling_service.poll_feed")
    def test_nickname_handling_in_polling(self, mock_poll):
        # 1. Nickname in aliases_map ('Man Utd' for 'Manchester United') -> MATCHED
        mock_poll.return_value = {
            "success": True,
            "entries": [{
                "url": "https://multisports.com/mufc-789",
                "title": "Man Utd appoints new assistant coach",
                "summary": "Premier League football update.",
                "published_at": timezone.now(),
            }, {
                "url": "https://multisports.com/spurs-101",
                "title": "Spurs secure top four spot",
                "summary": "Premier League football update.",
                "published_at": timezone.now(),
            }]
        }
        from apps.feed.tasks.polling import poll_single_source
        poll_single_source(self.source.id)

        # Man Utd matched via alias map
        mufc_item = FeedItem.objects.filter(url="https://multisports.com/mufc-789").first()
        self.assertIsNotNone(mufc_item)
        self.assertIn(self.mufc_team.id, set(mufc_item.entities.values_list("id", flat=True)))

        # Spurs is unmapped nickname (not in aliases_map), so it is safely skipped without wrong candidate dump
        spurs_item = FeedItem.objects.filter(url="https://multisports.com/spurs-101").first()
        self.assertIsNone(spurs_item)


