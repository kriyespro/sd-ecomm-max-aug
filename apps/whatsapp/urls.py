from django.urls import path

from . import views

app_name = "whatsapp"

urlpatterns = [
    path("webhook/", views.WebhookView.as_view(), name="webhook"),
]
