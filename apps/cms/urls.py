from django.urls import path

from . import views

app_name = "cms"

urlpatterns = [
    path("store/config/", views.StoreConfigView.as_view(), name="store_config"),
    path("store/navigation/<str:location>/", views.NavigationView.as_view(), name="navigation"),
    path("store/pages/<slug:slug>/", views.PageDetailView.as_view(), name="page_detail"),
    path("sitemap.xml", views.SitemapView.as_view(), name="sitemap"),
    path("robots.txt", views.RobotsView.as_view(), name="robots"),
]
