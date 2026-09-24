from django.urls import path

from apps.analyses import views

urlpatterns = [
    path("analyses", views.AnalysisListView.as_view()),
    path("analyses/<uuid:job_id>", views.AnalysisDetailView.as_view()),
    path("analyses/<uuid:job_id>/result", views.AnalysisResultView.as_view()),
]
