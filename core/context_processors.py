def wallet(request):
    user = request.user
    if user.is_authenticated and hasattr(user, "profile"):
        return {
            "wallet_balance": user.profile.balance,
            "is_provider": user.vendors.exists() or user.tutor_profiles.exists(),
            "unread_count": user.notifications.filter(read=False).count(),
        }
    return {}
