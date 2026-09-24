from django.urls import path

from apps.uploads import views

urlpatterns = [
    path("uploads", views.UploadListView.as_view()),
    path("uploads/<uuid:upload_id>", views.UploadDetailView.as_view()),
    path("uploads/<uuid:upload_id>/complete", views.UploadCompleteView.as_view()),
]
