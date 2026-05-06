# MCP Reference

Guidance for using the Model Context Protocol — when to reach for MCP vs rolling your own tool schemas, what MCP adds beyond standard tool use, and how to implement servers and clients in Python. For the underlying tool-use mechanics MCP builds on, see [`TOOL_USE.md`](TOOL_USE.md).

---

## What MCP is

MCP (Model Context Protocol) is a standard for connecting LLM clients to external capabilities. An **MCP server** exposes tools, resources, and prompts; an **MCP client** (inside your application) talks to the server using a defined message format.

The point: shift the burden of authoring tool schemas and execution logic from your application to a reusable server. If you're integrating with GitHub, AWS, Sentry, Slack, or any service with real breadth, someone else's MCP server is usually already doing the schema work for you.

- **Without MCP:** you write a schema + implementation for every endpoint you care about, maintain them as the upstream API evolves, and repeat the work across every app you build.
- **With MCP:** you point your MCP client at the relevant MCP server and get the full surface area as a managed catalog. Schema maintenance is someone else's problem.

---

## MCP vs raw tool use

Both end at the same place — Claude calls a function, you return a result. The difference is *who owns* the function's schema and runtime.

- **Raw tool use.** Your app owns the schema (you write it), the execution (you implement it), and the maintenance (you update it). Fine for small, bespoke integrations: internal APIs, a couple of custom calculators, domain-specific tools with tight coupling to your app.
- **MCP.** An MCP server owns the schema and execution. Your app embeds a client that routes tool calls to the server. Right for broad integrations, especially ones maintained by the service provider.

Raw tool use scales poorly in breadth — every new integration is hand-maintained surface area. MCP scales poorly in latency and opacity — an extra network hop and a server you didn't write. Pick based on breadth: narrow custom tools stay raw; broad third-party integrations use MCP.

---

## What MCP servers expose

Three primitives, each controlled by a different actor.

### Tools — model-controlled

Functions the model decides when to call. Same semantics as raw tool use — the model decides when to invoke, the server executes, results return. The difference is the schema comes from the server, not your code.

The Python MCP SDK uses a `@mcp.tool` decorator; JSON schemas are auto-generated from Python type annotations and `Field` descriptions. This removes the hand-written JSON-schema authoring that raw tool use demands.

```python
from mcp import FastMCP
from pydantic import Field

mcp = FastMCP("my-server")

@mcp.tool()
def read_document(
    doc_id: str = Field(description="The document identifier to retrieve"),
) -> str:
    if doc_id not in docs:
        raise ValueError(f"Document '{doc_id}' not found")
    return docs[doc_id]

@mcp.tool()
def edit_document(
    doc_id: str = Field(description="The document to edit"),
    old_string: str = Field(description="Exact text to replace"),
    new_string: str = Field(description="Replacement text"),
) -> str:
    if doc_id not in docs:
        raise ValueError(f"Document '{doc_id}' not found")
    docs[doc_id] = docs[doc_id].replace(old_string, new_string)
    return "Document updated"
```

### Resources — app-controlled

Read-only data the server exposes by URI. Two kinds:

- **Direct resources** (`docs://documents`) — static identifier, returns fixed data.
- **Templated resources** (`docs://documents/{doc_id}`) — parameterized identifier; the SDK parses parameters and routes to the handler.

The key distinction vs tools: resources are **pulled by the client** (typically when the user references them, e.g., `@filename` in a chat UI), not **requested by the model**. Use resources when data should be available in context on user demand. Use tools when the model should decide whether to fetch.

```python
@mcp.resource("docs://documents", mime_type="application/json")
def list_documents() -> list[str]:
    return list(docs.keys())

@mcp.resource("docs://documents/{doc_id}", mime_type="text/plain")
def get_document(doc_id: str) -> str:
    if doc_id not in docs:
        raise ValueError(f"Document '{doc_id}' not found")
    return docs[doc_id]
```

MIME type is a hint to the client about how to deserialize the response. Set `application/json` for structured data, `text/plain` for prose. The SDK auto-serializes return values to strings.

### Prompts — user-controlled

Pre-authored prompt templates the server exposes to the client. Server authors ship tested, optimized prompt-engineering work that users can invoke via slash commands or UI buttons — without writing the prompt themselves.

```python
from mcp.types import Message

@mcp.prompt(name="format", description="Rewrites a document as clean markdown")
def format_document(doc_id: str) -> list[Message]:
    return [
        {"role": "user", "content": f"Read document '{doc_id}' and rewrite it as clean, well-structured markdown. Preserve all information but improve formatting."}
    ]
```

The messages returned are sent directly to Claude as the conversation input. Prompts can reference tools to load dynamic data — e.g., the format prompt above works with a `read_document` tool to fetch content.

---

## Primitive control model

The three primitives serve different principals:

| Primitive | Controlled by | Use when |
|-----------|--------------|----------|
| Tools | Model — Claude decides when to call | You need Claude to gain a capability (run code, query an API) |
| Resources | App — application code fetches them | You need data in your UI or want to pre-load context for prompts |
| Prompts | User — triggered by clicks or slash commands | You want predefined workflows users can invoke without writing prompts |

---

## MCP client integration

Your application embeds an MCP client that speaks the protocol to one or more servers. Wrap `ClientSession` in your own class for resource cleanup — don't use it directly in business logic.

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

class MCPClient:
    def __init__(self):
        self.session: ClientSession | None = None

    async def connect(self, server_script: str):
        params = StdioServerParameters(command="python", args=[server_script])
        self._transport = stdio_client(params)
        read, write = await self._transport.__aenter__()
        self.session = ClientSession(read, write)
        await self.session.__aenter__()
        await self.session.initialize()

    async def cleanup(self):
        if self.session:
            await self.session.__aexit__(None, None, None)
        await self._transport.__aexit__(None, None, None)

    async def list_tools(self):
        result = await self.session.list_tools()
        return result.tools

    async def call_tool(self, tool_name: str, tool_input: dict):
        return await self.session.call_tool(tool_name, tool_input)

    async def read_resource(self, uri: str):
        from pydantic import AnyUrl
        result = await self.session.read_resource(AnyUrl(uri))
        resource = result.contents[0]
        if resource.mime_type == "application/json":
            import json
            return json.loads(resource.text)
        return resource.text

    async def list_prompts(self):
        result = await self.session.list_prompts()
        return result.prompts

    async def get_prompt(self, prompt_name: str, arguments: dict):
        result = await self.session.get_prompt(prompt_name, arguments)
        return result.messages
```

Typical flow for tool use: on connection, call `list_tools()` to get schemas, hand them to Claude as the tools list, then run the standard tool-use loop. When Claude requests a tool, route it via `call_tool()` instead of calling local code.

---

## Transport choices

MCP is transport-agnostic — client and server can talk over:

- **stdio** — server runs as a subprocess, client speaks over stdin/stdout. Right for local development, CLI tools, and servers that bundle with the application.
- **HTTP / SSE** — server runs as a network service. Right for shared servers, cloud-hosted servers, multi-tenant use cases.
- **WebSockets** — bidirectional stream. Right for long-lived sessions with server-pushed updates.

Default to stdio for local tools and HTTP for hosted services. The protocol semantics are identical; only the wire format changes.

---

## Debugging with the MCP Inspector

The MCP Inspector is a browser-based debugger for MCP servers. Run `mcp dev <server_file.py>` with the Python environment active and it opens an in-browser UI with:

- A connect button and connection status
- Tool, resource, and prompt catalogs
- Parameter-entry forms for manual invocation
- Success/failure feedback for each call

Use it to validate a server before wiring it into an application. If a tool fails in the Inspector, it will fail in production; if it works there, the remaining debugging is on the client side.

---

## When to reach for MCP

**Good fit:**

- Integrating with a service that already has a maintained MCP server (GitHub, Jira, Sentry, Slack, AWS, etc.). Someone else did the schema work.
- Building a product that wants to be extensible — users or admins can plug in new capabilities without editing your code. MCP servers are the plugin format.
- Consolidating internal tool definitions across multiple apps. One MCP server, many clients.

**Bad fit:**

- A single small integration with a custom internal API. Running a server for two tools is not worth the overhead; raw tool use is simpler.
- Latency-sensitive hot paths. The extra hop matters more than schema-maintenance savings.
- Very tight control over tool behavior per-request. MCP standardizes, which means standardizing away from custom per-request behavior.

---

## Relationship to Claude Code

Claude Code is itself an MCP client. `claude mcp add <name> <startup-command>` adds an MCP server to its capability set; tools and resources from that server become available inside Claude Code's tool catalog.

This means any MCP server you write extends Claude Code without modifying Claude Code. It's the right abstraction when you want to give Claude Code new capabilities (document readers, custom dev tooling, internal service integrations) rather than forking the tool itself.
