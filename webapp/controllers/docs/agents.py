from datetime import datetime
from systems.agents import Agent, AgentType, AgentsList, Foo, CreateAgentInput, CreateAgentOutput
from uuid import UUID, uuid4
from blacksheep.server.openapi.common import ContentInfo, EndpointDocs, HeaderInfo, RequestBodyInfo, ResponseExample, ResponseInfo

from webapp.errors import HttpError

get_agent_docs = EndpointDocs(
    summary="Gets a agent by id",
    description="""A sample API that uses a petstore as an
          example to demonstrate features in the OpenAPI 3 specification""",
    responses={
        200: ResponseInfo(
            "A agent",
            content=[
                ContentInfo(
                    Agent,
                    examples=[
                        ResponseExample(
                            Agent(
                                id=UUID("3fa85f64-5717-4562-b3fc-2c963f66afa6"),
                                name="Foo",
                                active=True,
                                type=AgentType.EUROPEAN,
                                creation_time=datetime.now(),
                            )
                        )
                    ],
                )
            ],
        ),
        404: "Agent not found",
    },
)


get_agents_docs = EndpointDocs(
    summary="Gets a page of agents",
    description="""Returns a paginated list of agents, including the total count of items
    that respect the given filters.
    """,
    responses={
        200: ResponseInfo(
            "A paginated set of agents",
            content=[
                ContentInfo(
                    AgentsList,
                    examples=[
                        AgentsList(
                            [
                                Agent(
                                    id=UUID("3fa85f64-5717-4562-b3fc-2c963f66afa6"),
                                    name="Foo",
                                    active=True,
                                    type=AgentType.EUROPEAN,
                                    creation_time=datetime.now(),
                                ),
                                Agent(
                                    id=UUID("f212cabf-987c-48e6-8cad-71d1c041209a"),
                                    name="Frufru",
                                    active=True,
                                    type=AgentType.PERSIAN,
                                    creation_time=datetime.now(),
                                ),
                            ],
                            101,
                        )
                    ],
                )
            ],
        ),
    },
)


create_agent_docs = EndpointDocs(
    request_body=RequestBodyInfo(
        description="Example description etc. etc.",
        examples={
            "fat_agent": CreateAgentInput(
                name="Fatty", active=False, type=AgentType.EUROPEAN, foo=Foo.FOO
            ),
            "thin_agent": CreateAgentInput(
                name="Thinny", active=False, type=AgentType.PERSIAN, foo=Foo.UFO
            ),
        },
    ),
    responses={
        201: ResponseInfo(
            "The agent has been created",
            headers={"Loagention": HeaderInfo(str, "URL to the new created object")},
            content=[
                ContentInfo(
                    CreateAgentOutput,
                    examples=[
                        ResponseExample(
                            CreateAgentOutput(uuid4()),
                            description="Something something",
                        ),
                        CreateAgentOutput(uuid4()),
                        CreateAgentOutput(uuid4()),
                    ],
                ),
            ],
        ),
        400: ResponseInfo(
            "Bad request",
            content=[
                ContentInfo(
                    HttpError,
                    examples=[
                        HttpError(
                            404,
                            "Bad request because something something",
                            "DUPLICATE_AGENT",
                        )
                    ],
                )
            ],
        ),
    },
)
