from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from apps.event.tasks import update_statpal_fixtures_for_dates

class Command(BaseCommand):
    help = "Fetch upcoming fixtures for all sports from StatPal and populate the database"

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=7,
            help='Number of upcoming days to fetch fixtures for (default: 7)'
        )
        parser.add_argument(
            '--past-days',
            type=int,
            default=1,
            help='Number of past days to fetch fixtures for (default: 1)'
        )

    def handle(self, *args, **options):
        days = options['days']
        past_days = options['past_days']

        self.stdout.write(self.style.NOTICE(f"Calculating date range: -{past_days} to +{days} days"))
        
        dates = [
            (timezone.now().date() + timedelta(days=i)).isoformat()
            for i in range(-past_days, days + 1)
        ]
        
        self.stdout.write(self.style.NOTICE(f"Dates to fetch: {dates}"))
        
        self.stdout.write(self.style.WARNING("Starting StatPal fixture synchronization..."))
        result = update_statpal_fixtures_for_dates(dates)
        
        self.stdout.write(self.style.SUCCESS(f"Finished: {result}"))
