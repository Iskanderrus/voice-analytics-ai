from django.urls import path

from apps.prompts import views

urlpatterns = [
    path("prompt-templates", views.PromptTemplateListView.as_view()),
    path("prompt-templates/<int:template_id>", views.PromptTemplateDetailView.as_view()),
    path(
        "prompt-templates/<int:template_id>/versions",
        views.PromptTemplateVersionsView.as_view(),
    ),
]
