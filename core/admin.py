from django.contrib import admin
from django.db.models import Sum

from .models import (MenuItem, Notification, Order, OrderItem, PaymentIntent, Profile, Rating, TutorProfile, Vendor,
                     WalletTransaction)


class MenuItemInline(admin.TabularInline):
    model = MenuItem
    extra = 1


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "owner", "location", "active")
    list_filter = ("category", "active")
    search_fields = ("name", "location")
    inlines = [MenuItemInline]


@admin.register(TutorProfile)
class TutorAdmin(admin.ModelAdmin):
    list_display = ("user", "module_code", "module_name", "hourly_rate", "active")
    list_filter = ("active",)
    search_fields = ("module_code", "module_name", "user__username")


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    readonly_fields = ("name", "unit_price", "quantity")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "kind", "customer", "provider", "amount", "commission", "stage", "created")
    list_filter = ("kind", "stage")
    search_fields = ("title", "customer__username")
    readonly_fields = ("customer", "kind", "vendor", "tutor", "provider", "amount", "commission", "created", "updated")
    inlines = [OrderItemInline]
    change_list_template = None

    def changelist_view(self, request, extra_context=None):
        done = Order.objects.filter(stage=Order.COMPLETED).aggregate(c=Sum("commission"), t=Sum("amount"))
        extra_context = extra_context or {}
        extra_context["title"] = (f"Orders  |  completed volume ${done['t'] or 0}  |  "
                                  f"GZU Connect commission earned ${done['c'] or 0}")
        return super().changelist_view(request, extra_context)


@admin.register(WalletTransaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "kind", "amount", "balance_after", "order", "reference", "created")
    list_filter = ("kind",)
    readonly_fields = [f.name for f in WalletTransaction._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(Profile)
admin.site.register(Rating)


@admin.register(PaymentIntent)
class PaymentIntentAdmin(admin.ModelAdmin):
    list_display = ("reference", "user", "amount", "method", "status", "credited", "created")
    list_filter = ("status", "method", "credited")
    readonly_fields = [f.name for f in PaymentIntent._meta.fields]

    def has_add_permission(self, request):
        return False


admin.site.register(Notification)
