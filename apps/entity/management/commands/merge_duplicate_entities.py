import time
from collections import defaultdict
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q, Count
from apps.entity.models import Entity, CanonicalEntity
from apps.entity.utils.normalizers import clean_team_prefix_suffix, normalize_entity_name
from apps.event.models import Event
from apps.nest.models import UserNest

SPORT_NORMALIZATION_MAP = {
    'american football': 'american_football',
    'football': 'american_football',
    'hockey': 'ice_hockey',
    'ice hockey': 'ice_hockey',
    'formula 1': 'f1',
    'formula1': 'f1',
    'mma': 'combat_sports',
    'fighting': 'combat_sports',
}


class Command(BaseCommand):
    """Management command to normalize sport choices, detect duplicate entities, and merge them into canonical records."""
    help = 'Normalize sport names, merge duplicate entities (e.g. NHL hockey vs ice_hockey, Roma vs AS Roma), re-link events and nest follows.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--commit',
            action='store_true',
            help='Actually commit canonical linking and data updates to the database'
        )
        parser.add_argument(
            '--sport',
            type=str,
            help='Filter scan by sport (e.g., soccer, ice_hockey, basketball)'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Number of duplicate groups to commit per transaction to keep CPU usage low'
        )
        parser.add_argument(
            '--sleep',
            type=float,
            default=0.01,
            help='Seconds to sleep between batches to protect CPU and prevent DB locks'
        )

    def handle(self, *args, **options):
        commit = options['commit']
        sport_filter = options.get('sport')
        batch_size = options.get('batch_size') or 100
        sleep_sec = options.get('sleep') or 0.01

        self.stdout.write(self.style.MIGRATE_HEADING("Step 1: Normalizing Sport Names Across Entities..."))
        norm_count = 0
        for old_sport, new_sport in SPORT_NORMALIZATION_MAP.items():
            matching_ents = Entity.objects.filter(sport=old_sport)
            c = matching_ents.count()
            if c > 0:
                self.stdout.write(f"  Mapping {c} entities with sport '{old_sport}' -> '{new_sport}'")
                if commit:
                    matching_ents.update(sport=new_sport)
                norm_count += c

        if commit and norm_count > 0:
            self.stdout.write(self.style.SUCCESS(f"Normalized {norm_count} entities to standard sport names."))
        elif not commit and norm_count > 0:
            self.stdout.write(self.style.WARNING(f"Would normalize {norm_count} entities to standard sport names (dry-run)."))

        self.stdout.write(self.style.MIGRATE_HEADING("\nStep 2: Scanning database for duplicate entities..."))

        qs = Entity.objects.filter(is_active=True)
        if sport_filter:
            qs = qs.filter(sport=sport_filter)

        # Precompute event counts for entities to prioritize entities with real match data
        event_counts = defaultdict(int)
        for row in Event.objects.values('home_entity_id').annotate(c=Count('id')):
            if row['home_entity_id']:
                event_counts[row['home_entity_id']] += row['c']
        for row in Event.objects.values('away_entity_id').annotate(c=Count('id')):
            if row['away_entity_id']:
                event_counts[row['away_entity_id']] += row['c']

        # Group entities by (normalized_sport, type, normalize_entity_name)
        grouped = defaultdict(list)
        for ent in qs.iterator(chunk_size=2000):
            ent_sport = SPORT_NORMALIZATION_MAP.get(ent.sport, ent.sport)
            norm_name = normalize_entity_name(ent.name)
            if norm_name:
                key = (ent_sport, ent.type, norm_name)
                grouped[key].append(ent)

        duplicate_groups = {k: v for k, v in grouped.items() if len(v) > 1}

        self.stdout.write(f"Scanned entities. Found {len(duplicate_groups)} duplicate group(s).")

        total_merged = 0
        updates_plan = []

        for (sport, ent_type, base_key), entities in duplicate_groups.items():
            # Scoring for best canonical entity:
            # 1. Has events linked
            # 2. Has logo_url
            # 3. api_source == 'statpal'
            # 4. Follower count
            # 5. Has external_id
            # 6. Lower ID
            sorted_entities = sorted(
                entities,
                key=lambda e: (
                    1 if event_counts.get(e.id, 0) > 0 else 0,
                    1 if e.logo_url else 0,
                    1 if e.api_source == 'statpal' else 0,
                    e.follower_count or 0,
                    1 if e.external_id else 0,
                    -e.id
                ),
                reverse=True
            )

            primary = sorted_entities[0]
            duplicates = sorted_entities[1:]

            group_info = {
                'primary': primary,
                'duplicates': duplicates,
                'sport': sport,
                'type': ent_type,
                'base_key': base_key,
                'primary_events': event_counts.get(primary.id, 0),
            }
            updates_plan.append(group_info)
            total_merged += len(duplicates)

        # Print report
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(f"TOTAL DUPLICATE GROUPS: {len(updates_plan)}")
        self.stdout.write(f"TOTAL DUPLICATE ENTITIES TO BE MERGED: {total_merged}")
        self.stdout.write("=" * 80 + "\n")

        sample_shown = 0
        for plan in updates_plan[:25]:
            p = plan['primary']
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"[Group] Sport: {plan['sport']} | Type: {plan['type']} | Base Key: '{plan['base_key']}'"
            ))
            self.stdout.write(self.style.SUCCESS(
                f"  Primary: ID #{p.id} - '{p.name}' (Source: {p.api_source}, Ext ID: {p.external_id}, Events: {plan['primary_events']}, Logo: {bool(p.logo_url)})"
            ))
            for d in plan['duplicates']:
                d_events = event_counts.get(d.id, 0)
                self.stdout.write(self.style.WARNING(
                    f"    -> Dup: ID #{d.id} - '{d.name}' (Source: {d.api_source}, Ext ID: {d.external_id}, Events: {d_events}, Logo: {bool(d.logo_url)})"
                ))

        if not commit:
            self.stdout.write(self.style.WARNING(
                "\nDRY-RUN ONLY: No database changes were made. Run with '--commit' to apply updates."
            ))
            return

        self.stdout.write(self.style.WARNING(f"\nAPPLYING CANONICAL LINKAGE & RE-LINKING FOR {total_merged} ENTITIES..."))

        events_relinked = 0
        nest_relinked = 0
        logos_copied = 0

        for i in range(0, len(updates_plan), batch_size):
            batch = updates_plan[i:i + batch_size]
            with transaction.atomic():
                for plan in batch:
                    primary = plan['primary']
                    primary_changed = False

                    # Canonical record
                    names_set = set(e.name for e in plan['duplicates'] + [primary])
                    canonical_rec, _ = CanonicalEntity.objects.get_or_create(
                        entity=primary,
                        defaults={
                            'sport': plan['sport'],
                            'entity_type': primary.type,
                            'canonical_name': primary.name,
                            'name_variations': list(names_set),
                            'external_ids': {primary.api_source: primary.external_id} if primary.api_source else {}
                        }
                    )
                    if canonical_rec:
                        existing_vars = set(canonical_rec.name_variations or [])
                        existing_vars.update(names_set)
                        canonical_rec.name_variations = list(existing_vars)
                        if primary.api_source and primary.external_id:
                            canonical_rec.external_ids[primary.api_source] = primary.external_id
                        canonical_rec.save(update_fields=['name_variations', 'external_ids'])

                    for dup in plan['duplicates']:
                        # Copy logo if primary doesn't have one
                        if not primary.logo_url and dup.logo_url:
                            primary.logo_url = dup.logo_url
                            primary_changed = True
                            logos_copied += 1

                        if not primary.cover_image_url and dup.cover_image_url:
                            primary.cover_image_url = dup.cover_image_url
                            primary_changed = True

                        # Link events from dup to primary
                        ev_home = Event.objects.filter(home_entity=dup).update(home_entity=primary)
                        ev_away = Event.objects.filter(away_entity=dup).update(away_entity=primary)
                        events_relinked += (ev_home + ev_away)

                        # Re-link UserNest followers
                        for un in UserNest.objects.filter(entity=dup):
                            if UserNest.objects.filter(user=un.user, entity=primary).exists():
                                un.delete()
                            else:
                                un.entity = primary
                                un.save(update_fields=['entity'])
                            nest_relinked += 1

                        # Mark duplicate with canonical_entity and deactivate from search
                        dup.canonical_entity = primary
                        dup.is_active = False
                        dup.sport = plan['sport']
                        dup.save(update_fields=['canonical_entity', 'is_active', 'sport'])

                    if primary.sport != plan['sport']:
                        primary.sport = plan['sport']
                        primary_changed = True

                    if primary_changed:
                        primary.save(update_fields=['logo_url', 'cover_image_url', 'sport'])

            if sleep_sec > 0:
                time.sleep(sleep_sec)
            processed_so_far = min(i + batch_size, len(updates_plan))
            if (i // batch_size) % 10 == 0 or processed_so_far == len(updates_plan):
                self.stdout.write(f"  Progress: {processed_so_far}/{len(updates_plan)} duplicate groups committed...")

        self.stdout.write(self.style.SUCCESS(
            f"\nSUCCESS:\n"
            f"  - Linked {total_merged} duplicate entities to their primary canonical entity.\n"
            f"  - Re-linked {events_relinked} events to primary entities.\n"
            f"  - Re-linked {nest_relinked} UserNest follows.\n"
            f"  - Copied {logos_copied} missing logos onto primary entities."
        ))
