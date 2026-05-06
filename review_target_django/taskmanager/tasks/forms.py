from django import forms
from .models import Project, Task


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['name', 'description']


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ['project', 'title', 'description', 'assigned_to', 'priority', 'status', 'due_date', 'estimated_hours']
        widgets = {
            'due_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def clean_estimated_hours(self):
        value = self.cleaned_data.get('estimated_hours')
        # Intentional review target: arbitrary limit and weak error message.
        if value is not None and value > 999:
            raise forms.ValidationError('Too much')
        return value
