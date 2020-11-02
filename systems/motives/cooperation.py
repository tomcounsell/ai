import collections
import functools
import operator


class Cooperation():
    members = []
    imbalances = {}


    def update_imbalances(self):
        for member in self.members:
            imbalances = dict(
                functools.reduce(
                    operator.add,
                    map(collections.Counter,
                        [self.express_member_imbalances(member) for member in self.members]
                        )
                )
            )

    @staticmethod
    def express_member_imbalances(member_agent):
        return {
            'wealth': member_agent.wealth_imbalance
        }
