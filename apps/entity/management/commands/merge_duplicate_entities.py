from collections import defaultdict
from django.core.management.base import BaseCommand
from django.db import transaction
from apps.entity.models import Entity, CanonicalEntity
from apps.entity.utils.normalizers import clean_team_prefix_suffix, normalize_entity_name


class Command(BaseCommand):
    help = 'Automatically detect duplicate entities (e.g. Roma vs AS Roma) and link them to a canonical entity.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--commit',
            action='store_true',
            help='Actually commit canonical linking updates to the database'
        )
        parser.add_argument(
            '--sport',
            type=str,
            help='Filter scan by sport (e.g., soccer, basketball, cricket)'
        )

    def handle(self, *args, **options):
        commit = options['commit']
        sport_filter = options.get('sport')

        self.stdout.write("Scanning database for duplicate entities...")

        qs = Entity.objects.filter(is_active=True)
        if sport_filter:
            qs = qs.filter(sport=sport_filter)

        # Group entities by (sport, type, base_clean_name)
        grouped = defaultdict(list)
        for ent in qs:
            base_key = clean_team_prefix_suffix(ent.name)
            if not base_key:
                base_key = normalize_entity_name(ent.name)
            if base_key:
                key = (ent.sport, ent.type, base_key)
                grouped[key].append(ent)

        duplicate_groups = {k: v for k, v in grouped.items() if len(v) > 1}

        self.stdout.write(f"Scanned {qs.count()} entities. Found {len(duplicate_groups)} duplicate group(s).")

        total_merged = 0
        updates_plan = []

        for (sport, ent_type, base_key), entities in duplicate_groups.items():
            # Determine primary entity:
            # 1. api_source == 'statpal'
            # 2. highest follower_count
            # 3. has external_id
            # 4. lowest id
            sorted_entities = sorted(
                entities,
                key=lambda e: (
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
            }
            updates_plan.append(group_info)
            total_merged += len(duplicates)

        # Report findings
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("DUPLICATE ENTITIES MERGE REPORT")
        self.stdout.write("=" * 80)

        for plan in updates_plan:
            p = plan['primary']
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"\n[Group] Sport: {plan['sport']} | Type: {plan['type']} | Base Key: '{plan['base_key']}'"
            ))
            self.stdout.write(self.style.SUCCESS(
                f"  Primary Canonical: ID #{p.id} - '{p.name}' (Source: {p.api_source}, Ext ID: {p.external_id})"
            ))
            for d in plan['duplicates']:
                self.stdout.write(self.style.WARNING(
                    f"    -> Link Duplicate: ID #{d.id} - '{d.name}' (Source: {d.api_source}, Ext ID: {d.external_id})"
                ))

        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(f"TOTAL DUPLICATE GROUPS: {len(updates_plan)}")
        self.stdout.write(f"TOTAL DUPLICATE ENTITIES TO BE CANONICALIZED: {total_merged}")
        self.stdout.write("=" * 80 + "\n")

        if not commit:
            self.stdout.write(self.style.WARNING(
                "DRY-RUN ONLY: No database changes were made. Run with '--commit' to apply updates."
            ))
            return

        # Execute DB changes
        self.stdout.write(self.style.WARNING(f"COMMITTING CANONICAL LINKAGE FOR {total_merged} ENTITIES..."))

        with transaction.atomic():
            for plan in updates_plan:
                primary = plan['primary']
                
                # Update or create CanonicalEntity helper record
                names_set = set(e.name for e in plan['duplicates'] + [primary])
                canonical_rec, _ = CanonicalEntity.objects.get_or_create(
                    entity=primary,
                    defaults={
                        'sport': primary.sport,
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

                # Link all duplicates to primary
                for dup in plan['duplicates']:
                    if dup.canonical_entity_id != primary.id:
                        dup.canonical_entity = primary
                        dup.save(update_fields=['canonical_entity'])

        self.stdout.write(self.style.SUCCESS(f"Successfully linked {total_merged} duplicate entities to primary canonical entities!"))
