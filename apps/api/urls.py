from rest_framework import routers
from apps.api.views import user

# API V1

app_name = 'api'
api_router = routers.DefaultRouter()

api_router.register(r'users', user.UserViewSet)
