from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle


class AuthThrottle(ScopedRateThrottle):
    scope = "auth"


class CheckoutThrottle(ScopedRateThrottle):
    scope = "checkout"


class WriteThrottle(UserRateThrottle):
    scope = "write"
