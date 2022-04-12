from blacksheep.server.controllers import Controller, get
from popoto import Model, KeyField, IntField
from systems.agent import Agent


class Counter(Model):
    name = KeyField()
    value = IntField(default=0, null=False)


class Home(Controller):
    @get()  # URL path = /
    def home(self):
        # returns uses home.html and context dict
        return self.view("home", {"agents": Agent.query.all(), "things": ["one", "two", "three", ]})

    @get("/counter")  # URL path = /counter
    def counter(self):

        # Since the view function is called without parameters, the name is
        # obtained from the calling request handler: 'example',
        # -> /views/home/example.html
        counter = Counter.query.get(name="main counter")
        if not counter:
            counter = Counter.create(name="main counter")
        counter.value += 1
        counter.save()

        return self.view("counter", {"count": counter.value})

    @get("/json")
    def json(self):
        from blacksheep.server.responses import json
        return json({"message": "Hello, World!"})
