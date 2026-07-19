from django.core.management.base import BaseCommand
from django.db import transaction
from apps.entity.models import Entity
from apps.feed.models import FeedItem
from apps.entity.utils.matcher import is_national_team
from apps.entity.utils.normalizers import normalize_entity_name
import re

class Command(BaseCommand):
    help = (
        "Cleanup non-sports related FeedItems currently incorrectly linked to "
        "national team entities (e.g. country name entities)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            dest='dry_run',
            default=False,
            help="Show the count of FeedItem-entity links that would be removed, without executing the actual deletion.",
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        
        sports_pattern = re.compile(
            r'\b(sport|game|match|play|coach|stadium|cup|tourn|leagu|champ|win|won|lost|lose|beat|defeat|scor|goal|team|club|socc|footb|crick|nba|nfl|mlb|nhl|baseb|hockey|tenn|golf|f1|formu|mma|ufc|fight|athlet|runn|race|olympi|squad|rost|train|seaso|jersey|manag|quali|friend|vs|draw|lineup|transf|victo|fan|ref|ump|offic|capt|skip|boss|injur|rule|pitch|ground|copa|americ|fifa|icc|strik|midf|defen|goalk|keep|batsm|bowl|clash|fixt|tie|legend|espn|cricinfo|espncricinfo|basketb|fiba|uefa|unbeat|sub|ban|card|assist|select|retir|ronaldo|messi|neymar|mbappe|shakib|cricbuzz|skysport|eurosport|talksport|theathletic|derby)\w*\b'
        )

        # 1. Find all active national team entities
        national_team_entities = [
            entity for entity in Entity.objects.filter(is_active=True)
            if is_national_team(entity.name)
        ]

        if not national_team_entities:
            self.stdout.write(self.style.WARNING("No active national team entities found."))
            return

        self.stdout.write(
            f"Found {len(national_team_entities)} national team entities: "
            f"{[e.name for e in national_team_entities]}"
        )

        affected_count = 0
        total_checked = 0

        # Loop through each national team entity and its linked FeedItems
        for entity in national_team_entities:
            feed_items = FeedItem.objects.filter(entities=entity)
            self.stdout.write(f"Evaluating {feed_items.count()} items linked to {entity.name}...")
            
            for item in feed_items:
                total_checked += 1
                text = f"{item.title} {item.summary or ''}".lower()
                text_norm = normalize_entity_name(text)
                
                # Check sports context using regex search
                if not sports_pattern.search(text_norm):
                    affected_count += 1
                    if dry_run:
                        self.stdout.write(
                            self.style.NOTICE(
                                f"[Dry-Run] Would remove '{entity.name}' from: '{item.title}'"
                            )
                        )
                    else:
                        with transaction.atomic():
                            item.entities.remove(entity)
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"Removed '{entity.name}' from: '{item.title}'"
                            )
                        )

        self.stdout.write("\n" + "=" * 50)
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"DRY-RUN completed. Total checked: {total_checked}. "
                    f"Would remove {affected_count} FeedItem-entity link(s)."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Cleanup completed. Total checked: {total_checked}. "
                    f"Successfully removed {affected_count} FeedItem-entity link(s)."
                )
            )
