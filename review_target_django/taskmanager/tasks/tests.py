from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from .models import Project, Task


class TaskSmokeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='tester', password='pass12345')
        self.project = Project.objects.create(name='Demo', description='Demo project', owner=self.user)
        self.task = Task.objects.create(project=self.project, title='Write tests', assigned_to=self.user)

    def test_task_list_requires_login(self):
        response = self.client.get(reverse('task_list'))
        self.assertEqual(response.status_code, 302)

    def test_task_list_after_login(self):
        self.client.login(username='tester', password='pass12345')
        response = self.client.get(reverse('task_list'))
        self.assertContains(response, 'Write tests')

    def test_mark_done(self):
        self.client.login(username='tester', password='pass12345')
        response = self.client.get(reverse('task_done', args=[self.task.id]))
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db()
        self.assertTrue(self.task.is_done)


class TaskApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='apiuser', password='pass12345')
        self.project = Project.objects.create(name='API Project', owner=self.user)
        self.client = Client()
        self.client.login(username='apiuser', password='pass12345')

    def test_api_list(self):
        Task.objects.create(project=self.project, title='API task')
        response = self.client.get(reverse('task_api'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('tasks', response.json())
