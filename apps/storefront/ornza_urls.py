from django.urls import re_path

from .ornza import serve_ornza

app_name = "ornza"

urlpatterns = [
    re_path(r"^(?P<path>.*)$", serve_ornza, name="asset"),
]
