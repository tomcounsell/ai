# Stage: build

**Goal.** A candidate that does what the plan says, with the tests the plan
named, committed in the workspace. Your prompt holds the critique's
verdict and any findings it left for the build.

**Exit evidence.** Every change committed (nothing left uncommitted or
untracked), then `.valor/done.md`: what you delivered, how you verified it,
and the decisions Tom might want to change, the one that most changes the
outcome first. The kernel records the head commit as the candidate, and
test, review, and docs check it before Tom sees it. A material question
goes in `.valor/question.md` instead.
