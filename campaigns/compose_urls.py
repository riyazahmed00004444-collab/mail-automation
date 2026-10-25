from django.urls import path
from . import compose_views

urlpatterns = [
    path('', compose_views.compose_view, name='compose'),
    path('direct/', compose_views.compose_direct_view, name='compose_direct'),
    path('direct/test-send/', compose_views.direct_test_send_api, name='direct_test_send_api'),
    path('direct/save-draft/', compose_views.direct_save_draft_api, name='direct_save_draft_api'),
    path('direct/preview-recipients/', compose_views.preview_recipients_api, name='preview_recipients_api'),
    path('generate/', compose_views.generate_draft, name='compose_generate'),
    path('<int:pk>/edit/', compose_views.compose_edit, name='compose_edit'),
]
