from uuid import UUID
from app.controllers.docs.agents import create_agent_docs, get_agent_docs, get_agents_docs
from systems.agent import Agent

from app.docs import docs
from blacksheep import Response
from blacksheep.server.bindings import FromJson, FromQuery
from blacksheep.server.controllers import ApiController, delete, get, patch, post


# In this case, the entity name is obtained from the class name: "agents"
# To specify a @classmethod called "class_name" and returning a string, like in the
# Foo example below.
class Agents(ApiController):
    @docs(get_agents_docs)
    @get()
    def get_agents(
        self,
        page: FromQuery[int] = FromQuery(1),
        page_size: FromQuery[int] = FromQuery(30),
        search: FromQuery[str] = FromQuery(""),
    ) -> AgentsList:
        """
        Returns a list of paginated agents.
        """

    @docs(get_agent_docs)
    @get("{agent_id}")
    def get_agent(self, agent_id: UUID) -> Agent:
        """
        Gets a agent by id.
        """

    @docs(summary="Updates a Agent")
    @patch("{agent_id}")
    def update_agent(self, agent_id: str, input: UpdateAgentInput) -> Agent:
        """
        Updates a agent with given id.
        """

    @post()
    @docs(create_agent_docs)
    def create_agent(self, input: FromJson[CreateAgentInput]) -> Agent:
        """
        Creates a new agent.
        """

    @docs(
        responses={
            204: "Agent deleted successfully",
        },
    )
    @delete("{agent_id}")
    def delete_agent(self, agent_id: str) -> Response:
        """
        Deletes a agent by id.

        Lorem ipsum dolor sit amet.
        """


class FooExample(ApiController):
    @classmethod
    def class_name(cls) -> str:
        return "foo"

    @docs.ignore()
    @get("{foo_id}")
    def get_foo(self, foo_id: str) -> Response:
        """
        Handles GET /api/foo/:id
        """
