from apps.assistant.models import Assistant, Conversation
from apps.item.models import Category


class AttachUserStateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        request.state = type("State", (), {})
        request.state.brands = []
        request.state.categories = []
        request.state.assistants = []
        request.state.conversations = []

        if request.user.is_authenticated:
            request.state.brands = request.user.brands.all()
            request.state.categories = Category.objects.filter(
                brand__in=request.state.brands
            )
            request.state.assistants = Assistant.objects.all()
            request.conversations = Conversation.objects.filter(
                assistant__in=request.state.assistants,
                brand__in=request.state.brands,
            )
