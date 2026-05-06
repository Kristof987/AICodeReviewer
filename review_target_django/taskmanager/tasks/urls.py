from django.contrib.auth import views as auth_views
from django.urls import path
from . import views

urlpatterns = [
    path('', views.task_list, name='task_list'),
    path('login/', auth_views.LoginView.as_view(template_name='tasks/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('projects/', views.project_list, name='project_list'),
    path('projects/new/', views.project_create, name='project_create'),
    path('projects/<int:project_id>/', views.project_detail, name='project_detail'),
    path('tasks/new/', views.task_create, name='task_create'),
    path('tasks/<int:task_id>/edit/', views.task_edit, name='task_edit'),
    path('tasks/<int:task_id>/done/', views.task_done, name='task_done'),
    path('tasks/<int:task_id>/delete/', views.task_delete, name='task_delete'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('api/tasks/', views.task_api, name='task_api'),
]
