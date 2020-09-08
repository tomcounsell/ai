

class World(object):

    def __init__(self):
        self.water
        self.food = 100
        self.time

        return

    def state(self):

        return

    def food_scavenged(self):
        food_allotment = 1
        self.food -= food_allotment
        return food_allotment

    def land_farmed(self):
        food_allotment = 5
        self.food += food_allotment
        return food_allotment

    # def food_farmed(self):


class Food(object):
    def __init__(self):
        self.good = True

    def time_passing(self):
        from random import randrange
        if randrange(1,5) == 1:
            self.expire()

    def expire(self):
        self.good = False

