from abc import ABC
from redisgraph import Node as GraphNode
from redisgraph import Edge as GraphEdge
from redisgraph import Graph, Path

from settings.redis_db import redis_db

class AbstractNode(ABC):

    graph_node = GraphNode()
    graph_edges = []


    def __init__(self, name):
        self.base_class = self.__class__.__name__
        self.graph_node = GraphNode(
            label='concept',
            properties={'id': 'uuid', 'importance': 1, 'active': True}
        )

    def _set_relationship_to_graphnode(self, context: dict, graph_node: GraphNode) -> None:
        self.graph_edges.append(
            GraphEdge(self.graph_node, 'correlation', graph_node, properties=context)
        )

    def save(self):
        redis_graph = Graph('concepts', redis_db)
        redis_graph.add_node(self.graph_node)
        for e in self.graph_edges:
            redis_graph.add_edge(e)
        redis_graph.commit()


class GenericNode(AbstractNode):

    def __init__(self, name):
        self.parent_class = None
        super(GenericNode, self).__init__(self, name)
