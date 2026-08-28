from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("verify/", views.VerifyCallbackView.as_view(), name="verify"),
    path("webhook/<str:provider>/", views.WebhookView.as_view(), name="webhook"),
]
