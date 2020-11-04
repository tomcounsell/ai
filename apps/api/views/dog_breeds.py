from rest_framework import authentication, permissions
from rest_framework.viewsets import ViewSet
from rest_framework.response import Response

from aihelps.skills.dog_breeds import DogBreedsSkill


class DogBreedsViewSet(ViewSet):
    """
    POST endpoint:

    - `/dog_breeds/` returns {"prediction": str, "breed": str, "confidence": float}

    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny | permissions.IsAuthenticated]

    def create(self, request):
        dog_breeds_agent = DogBreedsSkill()
        breed = dog_breeds_agent.name_breed_from_image_url(request.data['image_url'])
        confidence = dog_breeds_agent.get_confidence()
        return Response({
            'prediction': f"{confidence:.2f}% confident this is a {breed}",
            'breed': str(breed),
            'confidence': float(confidence)
        })
