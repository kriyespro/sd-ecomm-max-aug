from django.contrib.auth import views as auth_views


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.jinja"
    redirect_authenticated_user = True


class LogoutView(auth_views.LogoutView):
    pass
