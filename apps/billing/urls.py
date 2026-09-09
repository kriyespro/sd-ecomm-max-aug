from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("webhook/razorpay/", views.BillingWebhookView.as_view(), name="webhook"),
]
