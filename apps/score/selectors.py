from django.db.models import Q
from apps.score.models import LiveScore
from apps.nest.models import UserNest
from apps.entity.models import Entity
from apps.event.models import Event


def get_user_live_scores_queryset(user, sport: str | None = None):
    """Retrieve active live scores strictly filtered for the user's followed Nest entities.

    Args:
        user: Django User instance.
        sport (str | None): Optional sport filter (case-insensitive).

    Returns:
        QuerySet[LiveScore]: Distinct live score instances ordered by descending update time.
    """
    if not user or not user.is_authenticated:
        return LiveScore.objects.none()

    user_nest_entity_ids = list(
        UserNest.objects.filter(user=user).values_list("entity_id", flat=True)
    )
    if not user_nest_entity_ids:
        return LiveScore.objects.none()

    # Expand canonical, child duplicates, and athlete teams
    nest_entities = list(
        Entity.objects.filter(id__in=user_nest_entity_ids).select_related(
            'canonical_entity', 'athlete_details__current_team'
        )
    )
    all_entity_ids = set(user_nest_entity_ids)
    team_names = set()

    dup_filters = Q()
    for ent in nest_entities:
        team_names.add(ent.name.lower().strip())
        dup_filters |= Q(name__iexact=ent.name, sport=ent.sport, type=ent.type)
        if ent.canonical_entity_id:
            all_entity_ids.add(ent.canonical_entity_id)
            if ent.canonical_entity:
                team_names.add(ent.canonical_entity.name.lower().strip())
        if ent.type == 'athlete':
            ad = getattr(ent, 'athlete_details', None)
            if ad and ad.current_team_id:
                all_entity_ids.add(ad.current_team_id)
                if ad.current_team:
                    team_names.add(ad.current_team.name.lower().strip())

    if dup_filters:
        duplicates = Entity.objects.filter(dup_filters).values_list("id", flat=True)
        all_entity_ids.update(duplicates)

    all_entity_ids = list(all_entity_ids)

    # Find live Event external_ids matching user's Nest entities
    live_event_external_ids = list(
        Event.objects.filter(
            status="live"
        ).filter(
            Q(home_entity_id__in=all_entity_ids)
            | Q(away_entity_id__in=all_entity_ids)
            | Q(league_id__in=all_entity_ids)
        ).values_list("external_id", flat=True)
    )

    name_q = Q()
    for tname in team_names:
        if tname and len(tname) >= 3:
            name_q |= Q(home_team__icontains=tname) | Q(away_team__icontains=tname)

    qs = LiveScore.objects.filter(status="live").filter(
        Q(external_id__in=live_event_external_ids) | name_q
    ).distinct().order_by("-updated_at")

    if sport:
        qs = qs.filter(sport=sport.lower())

    return qs


# Backward-compatible alias
_get_user_live_scores_queryset = get_user_live_scores_queryset
