from django.contrib import admin

from .models import Membership, Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "phone", "platform_role", "is_banned")
    list_filter = ("platform_role", "is_banned")
    search_fields = ("user__username", "user__email", "phone")


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "project", "role", "is_active")
    list_filter = ("role", "is_active", "project")
    search_fields = ("user__username", "user__email", "project__name")
