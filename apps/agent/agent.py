from abc import ABC
from redisgraph import Node, Edge, Graph, Path

from settings.redis_db import redis_db


class Concept(ABC):
    reprensentation = bytes()
    node = Node()
    edges = []

    def __init__(self):
        self.node = Node(
            label='concept',
            properties={'id': 'uuid', 'reprensentation': self.reprensentation, 'importance': 1, 'active': True}
        )

    def set_contextual_relationship(self, context: dict, concept: 'Concept') -> None:
        self.edges.append(
            Edge(self.node, 'relation', concept.node, properties=context)
        )

    def save(self):
        redis_graph = Graph('ideas', redis_db)
        for n in self.nodes:
            redis_graph.add_node(n)
        for e in self.edges:
            redis_graph.add_edge(e)
        redis_graph.commit()
