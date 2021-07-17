from systems.structures.social_graph import Node


class Agent(Node):

    stimulus_subscriptions = []

    def set_partner(self, context: dict, agent: 'Agent') -> None:
        super()._set_relationship_to_graphnode(context, agent.graph_node)

    def publish_prediction(self):
        pass

    def subscribe_to_stimulus(self):
        pass

    def update_representation(self):
        pass


class Concept(Node):

    def set_correlate(self, context: dict, concept: 'Concept') -> None:
        super()._set_relationship_to_graphnode(context, concept.graph_node)


class ThinkingFastAndSlow:
    """
    Are these separate categories of agents OR alternative modes for running any agent?

    Fast, Instinctual Mind
    - generalized guessing
    - trained on everything under the sun
    - like GPT-3
    - every response/guess has a confidence value
    - add quantum computer?
    Slow, Contemplative Mind
    Computation Engine - a toolset for certifying the validity of anything
    - algorithms for calculations in math and physics
    - Hard-coded methods and grammars
    - goal to comprehend the governing laws of all nations
    """
