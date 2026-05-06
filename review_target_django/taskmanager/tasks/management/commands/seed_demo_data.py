from datetime import timedelta
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone
from tasks.models import Project, Task


class Command(BaseCommand):
    help = 'Seed demo data for the review target application.'

    def handle(self, *args, **options):
        admin, _ = User.objects.get_or_create(username='admin', defaults={'is_staff': True, 'is_superuser': True})
        admin.set_password('admin12345')
        admin.is_staff = True
        admin.is_superuser = True
        admin.save()

        alice, _ = User.objects.get_or_create(username='alice')
        alice.set_password('alice12345')
        alice.save()

        bob, _ = User.objects.get_or_create(username='bob')
        bob.set_password('bob12345')
        bob.save()

        p1, _ = Project.objects.get_or_create(name='Website redesign', defaults={'owner': alice, 'description': 'Refresh landing page and dashboard.'})
        p2, _ = Project.objects.get_or_create(name='Internal tooling', defaults={'owner': bob, 'description': 'Small automations for the team.'})

        if Task.objects.count() == 0:
            Task.objects.create(project=p1, title='Create wireframes', assigned_to=alice, priority='high', due_date=timezone.now().date() - timedelta(days=1), estimated_hours=5)
            Task.objects.create(project=p1, title='Implement homepage', assigned_to=bob, priority='medium', estimated_hours=12)
            Task.objects.create(project=p2, title='Export monthly report', assigned_to=alice, priority='low', estimated_hours=2)
            Task.objects.create(project=p2, title='Clean legacy script', assigned_to=bob, priority='high', status='in_progress', estimated_hours=8)

        self.stdout.write(self.style.SUCCESS('Demo data created.'))
