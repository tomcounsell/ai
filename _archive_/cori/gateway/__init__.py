"""LLM gateway: per-Brief tokens, budget, trace, kill, caching. Tech stack §4.

Seams §3.9 names four module-level things: `issue_token`, `revoke`,
`count_tokens`, and `app`. Each delegates to a default `Gateway` built on
first use from the Keychain key, the seat file, and the local database, so
the kernel can call `gateway.issue_token(conn, ...)` while the tests build
their own instance against a fake upstream and a fake tree.

A `Gateway` instance is what the tree's `delegate` and `stop` take as their
`TokenIssuer` (seams §3.2); the module is what `token_issuer=None` means.
"""

from pydantic import SecretStr

from gateway.core import Gateway
from schemas.budget import Budget
from schemas.ids import SpaceId

__all__ = [
    "Gateway",
    "app",
    "count_tokens",
    "default_gateway",
    "issue_token",
    "revoke",
    "serve",
]

_default: Gateway | None = None


def default_gateway() -> Gateway:
    """The process's one gateway. Built on first use, because reading the
    Keychain and the seat file at import time would make importing this
    module a side effect."""
    global _default
    if _default is None:
        from infra.secrets import read_secret

        _default = Gateway(provider_key=read_secret("anthropic_api_key"))
    return _default


def set_default_gateway(gateway: Gateway | None) -> None:
    global _default
    _default = gateway


async def issue_token(
    conn,
    *,
    brief_id: str,
    generation: int,
    model_ref: str,
    space: SpaceId,
    cap: Budget | None = None,
) -> SecretStr:
    return await default_gateway().issue_token(
        conn,
        brief_id=brief_id,
        generation=generation,
        model_ref=model_ref,
        space=space,
        cap=cap,
    )


async def revoke(conn, brief_id: str) -> None:
    return await default_gateway().revoke(conn, brief_id)


async def count_tokens(model_ref: str, body: dict) -> int:
    return await default_gateway().count_tokens(model_ref, body)


def __getattr__(name: str):
    """`app` and `serve` arrive with tasks 6 and 10; until then this module
    imports without them."""
    if name == "app":
        from gateway.app import build_app

        return build_app(default_gateway())
    if name == "serve":
        from gateway.serving import serve

        return serve
    raise AttributeError(name)
