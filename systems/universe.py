from systems.agent.population import Population
from systems.stimulus.environment import Environment
import time


class Universe:
    def __init__(self):
        self.started_at = time.time()
        with Environment() as environment:
            with Population() as population:
                for i in range(100):
                    next(environment)
                    next(population)
                    time.sleep(0.1)  # be nice to the system :)
