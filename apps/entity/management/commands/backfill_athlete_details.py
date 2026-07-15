import json
import time
import requests
from datetime import datetime
from django.core.management.base import BaseCommand
from apps.entity.models import Entity, Athlete
from apps.sports_apis.services.statpal import statpal_service

class Command(BaseCommand):
    help = 'Backfill missing soccer athlete details from StatPal API'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit the number of athletes processed'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Fetch and print raw API response without updating database'
        )

    def handle(self, *args, **options):
        limit = options['limit']
        dry_run = options['dry_run']

        self.stdout.write("Finding soccer athlete entities with has_api_data=False...")
        
        athletes_qs = Entity.objects.filter(
            sport='soccer',
            type='athlete',
            has_api_data=False
        ).exclude(
            external_id=''
        ).order_by('id')

        total_count = athletes_qs.count()
        self.stdout.write(f"Found {total_count} matching athletes in database.")

        if limit:
            athletes_qs = athletes_qs[:limit]
            self.stdout.write(f"Limiting to first {limit} athletes.")

        processed = 0
        failed = 0
        skipped = 0
        valid_images_count = 0
        invalid_images_count = 0

        for entity in athletes_qs:
            player_id = entity.external_id
            self.stdout.write(f"\nProcessing {entity.name} (ID: {entity.id}, StatPal Player ID: {player_id})...")

            try:
                # Call StatPal service (API Call 1)
                res = statpal_service.get_soccer_player(player_id)
                if not res.get('success'):
                    self.stdout.write(self.style.ERROR(f"Failed to fetch {entity.name}: {res.get('error')}"))
                    failed += 1
                    continue

                raw_data = res.get('data', {})
                player_dict = raw_data.get('player', {})
                if not player_dict:
                    player_dict = raw_data

                # Construct photo URL directly from StatPal API format
                photo_url = f"https://statpal.io/api/v2/soccer/images?type=player&id={player_id}&access_key={statpal_service.access_key}"
                
                # Respect rate limit — sleep 1.0 second between API Call 1 and API Call 2
                time.sleep(1.0)

                # Verify image URL (API Call 2)
                is_valid_image = False
                try:
                    img_resp = requests.get(photo_url, stream=True, timeout=5)
                    if img_resp.status_code == 200 and 'image' in img_resp.headers.get('Content-Type', '').lower():
                        is_valid_image = True
                    else:
                        is_valid_image = False
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"Image check failed for {entity.name}: {e}"))
                    is_valid_image = False

                if is_valid_image:
                    valid_images_count += 1
                else:
                    invalid_images_count += 1

                if dry_run:
                    self.stdout.write(self.style.SUCCESS(
                        f"Dry-run data retrieved successfully for {entity.name}. "
                        f"Image valid: {is_valid_image} (Status: {img_resp.status_code if 'img_resp' in locals() else 'error'})"
                    ))
                    processed += 1
                    # Respect rate limit — sleep 1.0 second at the end of the loop iteration
                    time.sleep(1.0)
                    continue

                # Normal run: Save to database
                bio = player_dict.get('bio') or player_dict.get('description') or ''
                country = player_dict.get('nationality') or player_dict.get('country') or player_dict.get('birthcountry') or ''
                
                # Update Entity model
                entity.logo_url = photo_url if is_valid_image else ''
                entity.description = bio
                entity.country = country
                entity.has_api_data = True
                entity.metadata = raw_data
                entity.save(update_fields=['logo_url', 'description', 'country', 'has_api_data', 'metadata'])

                # Update Athlete detail model
                athlete_obj, created = Athlete.objects.get_or_create(entity=entity)
                
                # Split name if name is provided in response, else use entity.name
                first_name = player_dict.get('firstname') or ''
                last_name = player_dict.get('lastname') or ''
                if not first_name and not last_name:
                    full_name = player_dict.get('name') or entity.name
                    names = full_name.split(maxsplit=1)
                    first_name = names[0] if names else ''
                    last_name = names[1] if len(names) > 1 else ''

                athlete_obj.first_name = first_name
                athlete_obj.last_name = last_name
                athlete_obj.nationality = country
                athlete_obj.position = player_dict.get('position') or athlete_obj.position or ''
                
                # Parse date of birth
                birthdate_str = player_dict.get('birthdate')
                if birthdate_str:
                    try:
                        athlete_obj.date_of_birth = datetime.strptime(birthdate_str, "%d.%m.%Y").date()
                    except Exception:
                        pass

                # Parse height
                height_str = player_dict.get('height')
                if height_str and str(height_str).strip() not in ('', 'None', 'null'):
                    try:
                        athlete_obj.height_cm = int(float(str(height_str).replace('cm', '').strip()))
                    except Exception:
                        athlete_obj.height_cm = None
                else:
                    athlete_obj.height_cm = None

                # Parse weight
                weight_str = player_dict.get('weight')
                if weight_str and str(weight_str).strip() not in ('', 'None', 'null'):
                    try:
                        athlete_obj.weight_kg = int(float(str(weight_str).replace('kg', '').strip()))
                    except Exception:
                        athlete_obj.weight_kg = None
                else:
                    athlete_obj.weight_kg = None

                # Parse jersey number from club statistics if available
                jersey = player_dict.get('jersey_number') or player_dict.get('jersey')
                if not jersey:
                    # Fallback to check statistics list
                    club_stats = player_dict.get('club_league_statistics', {})
                    club_list = club_stats.get('club', [])
                    if isinstance(club_list, dict):
                        club_list = [club_list]
                    elif not isinstance(club_list, list):
                        club_list = []
                    
                    for stat in club_list:
                        num = stat.get('jersey_number') or stat.get('jersey')
                        if num and str(num).isdigit():
                            jersey = num
                            break

                if jersey:
                    try:
                        athlete_obj.jersey_number = int(jersey)
                    except Exception:
                        pass

                athlete_obj.save()
                processed += 1
                self.stdout.write(self.style.SUCCESS(f"Successfully backfilled {entity.name}"))

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error processing athlete {entity.name}: {e}"))
                failed += 1

            # Respect rate limit — sleep 1 second between API calls
            time.sleep(1.0)

            # Print status periodically
            if processed % 10 == 0:
                self.stdout.write(f"--- Status: Processed {processed}/{total_count if not limit else limit}, {failed} failed ---")

        self.stdout.write(f"\nBatch completed: {processed} processed, {failed} failed, {skipped} skipped.")
        self.stdout.write(f"Image verification results: {valid_images_count} valid, {invalid_images_count} invalid (404/non-image).")
