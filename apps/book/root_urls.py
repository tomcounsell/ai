"""Root URL configuration for the blendedworkforce.ai domain.

This module is set as ``request.urlconf`` by ``DomainRoutingMiddleware``
when the incoming hostname matches a book domain.  It includes
``apps.book.urls`` under the ``book`` namespace so that template tags
like ``{% url 'book:landing' %}`` work identically whether the request
arrives on the book domain or the Cuttlefish domain.
"""

from django.urls import include, path

from apps.api.views.health_views import deep_health_check, health_check

urlpatterns = [
    path("", include("apps.book.urls", namespace="book")),
    # Health checks must be available on all domains
    path("health/", health_check, name="health_check"),
    path("health/deep/", deep_health_check, name="deep_health_check"),
]
