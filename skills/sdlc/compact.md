This is a chat session's conversation, and the summary is all it keeps of
what came before. Write it in these parts, the whole within about 12,000
tokens, so that a summary updated again and again never outgrows it.

1. **What is still true** (about 2,000 tokens): who is in the chat and what
   each cares about; every ask not yet answered and who made it; each task
   the session started, with its id, what it is for, who asked and where it
   is; and what the session promised the chat. When an earlier summary is
   in the conversation, start from it: keep what is still true and drop
   what has been settled.
2. **What the humans said, verbatim, newest first**: each human message in
   the chat, with when, who and its message id, quoted exactly, carrying
   forward the quotes from an earlier summary. About 24,000 characters in
   all; cut any one message at 4,000 characters, marking the cut; past
   the cap drop the oldest. Leave out messages that part 3 holds. A
   dropped message is still in the chat (`valor-telegram read`,
   `valor-slack read`) and in Valor's record.
3. **The recent tail, verbatim**: the chat's last 20 messages, oldest
   first, the humans' and the session's own sends, each with when, who and
   its message id, within about 3,000 tokens. Cut any one at 4,000
   characters; when the budget runs out, keep fewer, the newest.
{opening}
