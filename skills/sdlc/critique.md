# Stage: critique

**Goal.** Find what the plan gets wrong while it is cheap to fix: a wrong
premise about the code, a case the tests will miss, scope that does not
serve the request, loop counts too low for the stakes.

You did not write the plan and do not read the session that did. You read
the request, Tom's answers, the plan file, and the code.

**Exit evidence.** A verdict, `sound` or `revise`, with every finding, and
either loop count raised if the stakes call for more (never lowered). A
`revise` sends the plan back while critique rounds remain; after that its
findings go to the build.
