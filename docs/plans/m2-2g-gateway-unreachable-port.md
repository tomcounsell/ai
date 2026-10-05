# 2.2g Gateway unreachable-port test

Tracking: none. Status: built.

`test_an_unreachable_upstream_is_a_502_naming_it` points the gateway at a
free port. It binds a socket to port 0, reads the port the system chose,
closes the socket, and uses that port as the upstream. Ports from port 0 come
from the ephemeral range, outside every agent block (6430 up, ten per
agent), and nothing listens on one just closed, so the connection is refused
at once.

A fixed port inside an agent block is not used: an agent's service can hold
it, and then the connection succeeds. A bound socket that never listens is
not used either. On macOS it does not refuse: the connect times out after
about 8 seconds.

Other tests with a port or "nothing listens" comment make no connection:
`test_targets.py` and `test_credential_push.py` (URL checks only), and
`test_look.py` 6451 (the browser is missing, so nothing connects).
`test_workspace.py` port 9 does connect, in a fetch the test expects to
fail; port 9 is outside the agent range and below any block, so no other
run holds it.
