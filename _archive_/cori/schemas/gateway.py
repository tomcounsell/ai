"""What the gateway records. Seams §1.16; tech stack §4.

`Usage` is the provider's four token fields plus what the gateway derives
from them: `usd_micros` at the seat's price with ceiling rounding per field,
and `charged_reserved`, true when the call ended without a `message_delta`
and was charged its reserved `max_tokens` (spike 03 surprise 2: a cut stream
never delivers output usage, and the provider billed a generation the
gateway did not see finish).

The three Literals name the values `gateway_log` holds (seams §5.1), so the
gateway, the worker adapter that reads a refusal reason off a 403, and the
integration tests that read the rows all spell them once.
"""

from typing import Literal

from schemas.space import Strict


class Usage(Strict, frozen=True):
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    usd_micros: int
    charged_reserved: bool


GatewayEvent = Literal[
    "request",
    "response",
    "cut",
    "refused",
    "upstream_error",
    "token_issued",
    "token_revoked",
]
RefusalReason = Literal[
    "unknown_token", "revoked", "model", "stale_generation", "budget"
]
CacheState = Literal["write", "read", "none", "unstable"]
