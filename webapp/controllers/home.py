from blacksheep.server.application import Application
from blacksheep.server.controllers import Controller, get
from blacksheep.messages import Response


class Home(Controller):
    @get()
    def home(self):
        # Since the @get() decorator is used without arguments, the URL path
        # is by default "/"

        # Since the view function is called without parameters, the name is
        # obtained from the calling request handler: 'home',
        # -> /views/home/home.html
        return self.view()

    @get(None)
    def example(self):
        # Since the @get() decorator is used explicitly with None, the URL path
        # is obtained from the method name: "/example"

        # Since the view function is called without parameters, the name is
        # obtained from the calling request handler: 'example',
        # -> /views/home/example.html
        return self.view()
