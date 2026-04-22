from rest_framework import serializers
from apps.feed.models import Source
from .models import UserCustomSource


class SourceSuggestionSerializer(serializers.Serializer):
    """Returned by the AI search endpoint — not a DB model yet."""
    name = serializers.CharField()
    domain = serializers.CharField()
    description = serializers.CharField()
    favicon_url = serializers.CharField()
    rss_url = serializers.CharField(allow_blank=True)
    has_rss = serializers.BooleanField()
    tags = serializers.ListField(child=serializers.CharField())
    # If this source already exists in DB, we include its id so the
    # frontend can show "Already Added" state
    source_id = serializers.IntegerField(allow_null=True, required=False)
    is_added = serializers.BooleanField(required=False, default=False)


class UserCustomSourceSerializer(serializers.ModelSerializer):
    """Full source detail for the user's source list."""
    source_id = serializers.IntegerField(source='source.id', read_only=True)
    name = serializers.CharField(source='source.name', read_only=True)
    domain = serializers.CharField(source='source.domain', read_only=True)
    rss_url = serializers.CharField(source='source.rss_url', read_only=True)
    favicon_url = serializers.CharField(source='source.favicon_url', read_only=True)
    is_healthy = serializers.BooleanField(source='source.is_healthy', read_only=True)
    last_polled_at = serializers.DateTimeField(source='source.last_polled_at', read_only=True)
    poll_failures = serializers.IntegerField(source='source.poll_failures', read_only=True)

    class Meta:
        model = UserCustomSource
        fields = [
            'id', 'source_id', 'name', 'domain', 'rss_url',
            'favicon_url', 'is_healthy', 'last_polled_at',
            'poll_failures', 'search_query', 'is_active', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']