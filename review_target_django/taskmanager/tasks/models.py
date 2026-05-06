from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class Project(models.Model):
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='owned_projects')
    created_at = models.DateTimeField(default=timezone.now)
    is_archived = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    def overdue_task_count(self):
        # Intentional review target: each call can trigger a query in templates/views.
        return self.tasks.filter(due_date__lt=timezone.now().date(), is_done=False).count()


class Task(models.Model):
    PRIORITY_LOW = 'low'
    PRIORITY_MEDIUM = 'medium'
    PRIORITY_HIGH = 'high'

    STATUS_TODO = 'todo'
    STATUS_IN_PROGRESS = 'in_progress'
    STATUS_DONE = 'done'

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='tasks')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    priority = models.CharField(max_length=20, default=PRIORITY_MEDIUM)
    status = models.CharField(max_length=20, default=STATUS_TODO)
    due_date = models.DateField(null=True, blank=True)
    is_done = models.BooleanField(default=False)
    estimated_hours = models.IntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

    def mark_done(self):
        self.is_done = True
        self.status = self.STATUS_DONE
        self.save()

    def is_overdue(self):
        if self.due_date is None:
            return False
        return self.due_date < timezone.now().date() and not self.is_done
