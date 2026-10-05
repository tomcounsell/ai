# 2.2g Gateway unreachable-port test

Tracking: none. Status: built.

`test_an_unreachable_upstream_is_a_502_naming_it` pointed the gateway at
`127.0.0.1:6561`, a port inside the 6430+ blocks handed to agents. When an
agent's service held it, the connection succeeded and the test failed.

The test now asks the system for a free port (bind to port 0, read the
port, close the socket) and points the gateway there. Ports from port 0 come
from the ephemeral range, outside every agent block, and nothing listens on
one just handed out, so the connection is refused at once.

A bound socket that never listens was tried first. On macOS it does not
refuse: the connect times out after about 8 seconds. It is not used.

Other tests with a port or "nothing listens" comment make no connection:
`test_targets.py` and `test_credential_push.py` (URL checks only),
`test_look.py` 6451 (the browser is missing, so nothing connects), and
`test_workspace.py` port 9 (outside the agent range, below any block).
