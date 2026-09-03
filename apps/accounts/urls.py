from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("signup/", views.SignupView.as_view(), name="signup"),
    path("signup/complete/", views.SignupCompleteView.as_view(), name="signup_complete"),
    path("google/start/", views.GoogleStartView.as_view(), name="google_start"),
    path("google/callback/", views.GoogleCallbackView.as_view(), name="google_callback"),
]
