from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/register/", views.register, name="register"),
    path("food/", views.food_list, name="food_list"),
    path("food/<int:pk>/", views.food_detail, name="food_detail"),
    path("print/", views.print_order, name="print"),
    path("laundry/", views.laundry_order, name="laundry"),
    path("tutors/", views.tutor_list, name="tutors"),
    path("tutors/<int:pk>/book/", views.tutor_book, name="tutor_book"),
    path("orders/", views.order_list, name="orders"),
    path("orders/<int:pk>/", views.order_detail, name="order_detail"),
    path("orders/<int:pk>/pay/", views.order_pay, name="order_pay"),
    path("orders/<int:pk>/cancel/", views.order_cancel, name="order_cancel"),
    path("orders/<int:pk>/rate/", views.order_rate, name="order_rate"),
    path("orders/<int:pk>/document/", views.order_document, name="order_document"),
    path("wallet/", views.wallet_view, name="wallet"),
    path("wallet/topup/<str:reference>/", views.topup_wait, name="topup_wait"),
    path("wallet/topup/<str:reference>/status/", views.topup_status, name="topup_status"),
    path("paynow/result/", views.paynow_result, name="paynow_result"),
    path("alerts/", views.notifications_view, name="alerts"),
    path("jobs/", views.jobs, name="jobs"),
    path("jobs/<int:pk>/advance/", views.job_advance, name="job_advance"),
]
