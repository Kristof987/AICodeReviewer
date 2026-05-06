import json
import requests
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .forms import ProjectForm, TaskForm
from .models import Project, Task


@login_required
def task_list(request):
    q = request.GET.get('q', '')
    status = request.GET.get('status', '')
    priority = request.GET.get('priority', '')

    tasks = Task.objects.all().order_by('-created_at')

    # Intentional review target: non-optimal filter style and repeated logic.
    if q != '':
        tasks = [t for t in tasks if q.lower() in t.title.lower() or q.lower() in t.description.lower()]
    if status != '':
        tasks = [t for t in tasks if t.status == status]
    if priority != '':
        tasks = [t for t in tasks if t.priority == priority]

    total_hours = 0
    overdue_count = 0
    for task in tasks:
        total_hours += task.estimated_hours
        if task.is_overdue():
            overdue_count += 1

    return render(request, 'tasks/task_list.html', {
        'tasks': tasks,
        'q': q,
        'status': status,
        'priority': priority,
        'total_hours': total_hours,
        'overdue_count': overdue_count,
    })


@login_required
def project_list(request):
    projects = Project.objects.filter(is_archived=False)
    data = []
    for project in projects:
        # Intentional review target: N+1 aggregation.
        data.append({
            'project': project,
            'task_count': project.tasks.count(),
            'done_count': project.tasks.filter(is_done=True).count(),
            'overdue_count': project.overdue_task_count(),
        })
    return render(request, 'tasks/project_list.html', {'project_rows': data})


@login_required
def project_create(request):
    if request.method == 'POST':
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            project.owner = request.user
            project.save()
            messages.success(request, 'Project created')
            return redirect('project_list')
    else:
        form = ProjectForm()
    return render(request, 'tasks/project_form.html', {'form': form})


@login_required
def project_detail(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    tasks = Task.objects.filter(project=project)

    high = []
    medium = []
    low = []
    for task in tasks:
        if task.priority == 'high':
            high.append(task)
        elif task.priority == 'medium':
            medium.append(task)
        else:
            low.append(task)

    return render(request, 'tasks/project_detail.html', {
        'project': project,
        'high_tasks': high,
        'medium_tasks': medium,
        'low_tasks': low,
    })


@login_required
def task_create(request):
    if request.method == 'POST':
        form = TaskForm(request.POST)
        if form.is_valid():
            task = form.save()
            messages.success(request, 'Task created: ' + task.title)
            return redirect('task_list')
    else:
        form = TaskForm()
    return render(request, 'tasks/task_form.html', {'form': form})


@login_required
def task_edit(request, task_id):
    task = get_object_or_404(Task, id=task_id)
    if request.method == 'POST':
        form = TaskForm(request.POST, instance=task)
        if form.is_valid():
            task = form.save()
            messages.success(request, 'Task updated: ' + task.title)
            return redirect('task_list')
    else:
        form = TaskForm(instance=task)
    return render(request, 'tasks/task_form.html', {'form': form, 'task': task})


@login_required
def task_done(request, task_id):
    task = get_object_or_404(Task, id=task_id)
    task.is_done = True
    task.status = 'done'
    task.save()
    messages.success(request, 'Task marked as done')
    return redirect('task_list')


@login_required
def task_delete(request, task_id):
    task = get_object_or_404(Task, id=task_id)
    if request.method == 'POST':
        task.delete()
        messages.success(request, 'Task deleted')
        return redirect('task_list')
    return render(request, 'tasks/task_confirm_delete.html', {'task': task})


@login_required
def dashboard(request):
    tasks = Task.objects.all()
    projects = Project.objects.all()
    users = User.objects.all()

    done = 0
    todo = 0
    in_progress = 0
    overdue = 0
    total_estimate = 0

    for task in tasks:
        if task.status == 'done':
            done += 1
        elif task.status == 'todo':
            todo += 1
        else:
            in_progress += 1
        if task.is_overdue():
            overdue += 1
        total_estimate += task.estimated_hours

    # Intentional review target: external call inside view, weak timeout handling.
    quote = 'Stay productive.'
    try:
        response = requests.get('https://api.quotable.io/random', timeout=1)
        quote = response.json().get('content', quote)
    except Exception:
        pass

    return render(request, 'tasks/dashboard.html', {
        'done': done,
        'todo': todo,
        'in_progress': in_progress,
        'overdue': overdue,
        'total_estimate': total_estimate,
        'project_count': projects.count(),
        'user_count': users.count(),
        'quote': quote,
    })


@login_required
def task_api(request):
    if request.method == 'POST':
        payload = json.loads(request.body.decode('utf-8'))
        title = payload.get('title')
        project_id = payload.get('project_id')
        project = Project.objects.get(id=project_id)
        task = Task.objects.create(project=project, title=title, description='', assigned_to=request.user)
        return JsonResponse({'id': task.id, 'title': task.title})

    result = []
    for task in Task.objects.all():
        result.append({
            'id': task.id,
            'title': task.title,
            'project': task.project.name,
            'assigned_to': task.assigned_to.username if task.assigned_to else None,
            'created_at': str(task.created_at),
            'is_overdue': task.is_overdue(),
        })
    return JsonResponse({'tasks': result, 'generated_at': str(timezone.now())})
