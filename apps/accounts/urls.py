from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("2fa/setup/", views.TwoFactorSetupView.as_view(), name="2fa_setup"),
    path("2fa/verify/", views.TwoFactorVerifyView.as_view(), name="2fa_verify"),
    path("signup/", views.SignupView.as_view(), name="signup"),
    path("signup/complete/", views.SignupCompleteView.as_view(), name="signup_complete"),
    path("signup/password/", views.PasswordSignupView.as_view(), name="signup_password"),
    path("signup/check-email/", views.SignupCheckEmailView.as_view(), name="signup_check_email"),
    path("signup/resend/", views.SignupResendView.as_view(), name="signup_resend"),
    path("signup/verify/<str:token>/", views.SignupVerifyView.as_view(), name="signup_verify"),
    path("invite/<str:token>/", views.TeamInviteAcceptView.as_view(), name="invite_accept"),
    path("affiliate/", views.AffiliateJoinView.as_view(), name="affiliate_join"),
    path("google/start/", views.GoogleStartView.as_view(), name="google_start"),
    path("google/callback/", views.GoogleCallbackView.as_view(), name="google_callback"),
]
