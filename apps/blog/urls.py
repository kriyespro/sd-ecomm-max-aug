from django.urls import path

from . import public_views as views

app_name = "blog"

urlpatterns = [
    path("", views.blog_index, name="index"),
    path("category/<slug:slug>/", views.blog_category, name="category"),
    path("tag/<slug:slug>/", views.blog_tag, name="tag"),
    path("<slug:slug>/", views.blog_detail, name="detail"),
]
