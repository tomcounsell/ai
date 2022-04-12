from rest_framework import routers
from apps.api.views import user, dog_breeds

# API V1

app_name = 'api'
api_router = routers.DefaultRouter()

api_router.register(r'users', user.UserViewSet)

api_router.register(r'dog_breeds', dog_breeds.DogBreedsViewSet, 'DogBreeds')
