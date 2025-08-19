from rest_framework import routers

app_name = "api"
api_router = routers.DefaultRouter()

# All API endpoints have been removed
urlpatterns = api_router.urls + [
    # Empty API - all endpoints have been removed
]
