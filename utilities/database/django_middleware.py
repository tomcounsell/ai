import os
class APIHeaderMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response['X-Required-Main-Build'] = os.environ.get('Required-Main-Build', "unknown")
        return response