from apps.agent.social_graph.node import Node


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
