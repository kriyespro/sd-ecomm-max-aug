from django.urls import path

from . import views

app_name = "shipping"

urlpatterns = [
    path("webhook/<str:courier>/", views.CourierWebhookView.as_view(), name="webhook"),
]
