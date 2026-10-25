"""
knowledge/views.py — CRUD for KnowledgeBase entries.
"""
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from .models import KnowledgeBase


@login_required
def knowledge_list(request):
    entries = KnowledgeBase.objects.all()
    return render(request, 'knowledge/list.html', {'entries': entries})


@login_required
def knowledge_create(request):
    if not request.user.is_staff:
        messages.error(request, 'Staff access required.')
        return redirect('knowledge_list')

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        content = request.POST.get('content', '').strip()
        is_active = request.POST.get('is_active') == 'on'

        if not title or not content:
            messages.error(request, 'Title and content are required.')
        else:
            KnowledgeBase.objects.create(title=title, content=content, is_active=is_active)
            messages.success(request, 'Entry created.')
            return redirect('knowledge_list')

    return render(request, 'knowledge/form.html', {'entry': None, 'action': 'Create'})


@login_required
def knowledge_edit(request, pk):
    if not request.user.is_staff:
        messages.error(request, 'Staff access required.')
        return redirect('knowledge_list')

    entry = get_object_or_404(KnowledgeBase, pk=pk)

    if request.method == 'POST':
        entry.title = request.POST.get('title', '').strip()
        entry.content = request.POST.get('content', '').strip()
        entry.is_active = request.POST.get('is_active') == 'on'
        entry.save()
        messages.success(request, 'Entry updated.')
        return redirect('knowledge_list')

    return render(request, 'knowledge/form.html', {'entry': entry, 'action': 'Edit'})


@login_required
@require_POST
def knowledge_delete(request, pk):
    if not request.user.is_staff:
        messages.error(request, 'Staff access required.')
        return redirect('knowledge_list')

    entry = get_object_or_404(KnowledgeBase, pk=pk)
    entry.delete()
    messages.success(request, 'Entry deleted.')
    return redirect('knowledge_list')
