# RSI plan spikes: provider accounts and venture liability

Two spikes run for the RSI plan (#3655) on 2026-10-20. They cover what the
four notes did not. The conclusions are in `docs/plans/rsi.md`; this page
keeps the findings and the sources.

## Provider accounts

**Finding (high confidence).** Valor cannot open a provider account on its
own. Each one needs a person at Yudame to pass the phone, card, ID or terms
step. After that, Valor can run the account.

- GitHub's terms ban accounts created by bots. Each account needs a human
  (https://docs.github.com/en/site-policy/github-terms/github-terms-of-service).
- Cloudflare's agent path through Stripe Projects still needs a human to
  accept the terms and add a card
  (https://blog.cloudflare.com/agents-stripe-projects/,
  https://www.cloudflare.com/terms/).
- Anthropic's commercial terms put the customer's account behind its
  agents (https://www.anthropic.com/legal/commercial-terms). The claude.ai
  signup needs a phone.
- OpenAI checks ID with a selfie and allows one organization per ID.
  Cerebras needs a card. Mistral needs a phone.
- Pooling free tiers across accounts is banned by OpenRouter
  (https://openrouter.ai/terms), Cloudflare and Groq
  (https://console.groq.com/docs/legal/services-agreement).
- Some free tiers train on inputs: Gemini
  (https://ai.google.dev/gemini-api/terms), Mistral
  (https://help.mistral.ai/en/articles/323757) and some OpenRouter `:free`
  models. Use them for open-source work only.

**Usable with no program:**

- Workers AI (Cloudflare's free allocation);
- Modal, $30 a month (https://modal.com/pricing);
- E2B, $100 once (https://e2b.dev/pricing);
- Daytona, $200 (https://www.daytona.io/pricing);
- GitHub Actions.

**Founder programs:** AWS Activate (https://aws.amazon.com/startups/credits)
and Microsoft for Startups (Azure). Both need a founder to apply.
Anthropic's startup cohort is closed. OpenAI's credits come only through
VCs.

**Not settled:**

- Exact free amounts change monthly, so they are read at signup.
- Whether Stripe Projects will drop its human step was not found.

## Venture liability

**Finding (medium confidence).** No US court has ruled on whether an
agent's act outside its scope binds the company behind it. Every source
puts the exposure on the deployer, here Yudame Inc.

- **Contracts.** UETA §14
  (https://en.wikipedia.org/wiki/Uniform_Electronic_Transactions_Act) and
  E-SIGN §7001(h) bind a party to contracts its electronic agent makes.
  The Restatement (Third) of Agency treats software as a tool, not an
  agent. See also
  https://www.proskauer.com/blog/contract-law-in-the-age-of-agentic-ai-whos-really-clicking-accept.
- **Statements.** *Moffatt v. Air Canada* (2024) held the airline liable
  for its chatbot's answer
  (https://www.americanbar.org/groups/business_law/resources/business-law-today/2024-february/bc-tribunal-confirms-companies-remain-liable-information-provided-ai-chatbot/).
- **No "the AI did it" defence.** California AB 316, in force 2026, bars it
  (https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202520260AB316).
- **Operator exposure.**
  - https://www.venable.com/insights/publications/2026/04/rogue-ai-agents-wont-be-testifying-you-will
  - https://www.bakermckenzie.com/en/insight/publications/2026/06/united-states-legal-accountability-for-ai-agents
- **Platform access.** The Ninth Circuit vacated the CFAA injunction
  against Perplexity's Comet agent, but the platforms' own terms still
  govern
  (https://www.jonesday.com/en/insights/2026/09/ninth-circuit-vacates-cfaa-injunction-against-perplexitys-comet-ai-agent).
- **Marketplace terms.**
  - Upwork bans bots (https://gigradar.io/blog/upwork-automation).
  - Fiverr sets AI content rules
    (https://help.fiverr.com/hc/en-us/articles/37333179414289-Community-Standards-AI-generated-content).
  - Stripe has its agentic seller terms
    (https://stripe.com/legal/stripe-agentic-commerce-seller-services).
- **Disclosure and claims.**
  - The California B.O.T. Act (https://servanda.ai/law/california/ca-bpc-17941).
  - EU AI Act Art. 50; the omnibus moved its deadlines
    (https://cdp.cooley.com/digital-ai-omnibus-delays-key-deadlines-introduces-new-rules/).
  - The FTC's Operation AI Comply
    (https://www.ftc.gov/news-events/news/press-releases/2024/09/ftc-announces-crackdown-deceptive-ai-claims-schemes,
    https://www.beneschlaw.com/insight/one-year-in-ftcs-operation-ai-comply-continues-under-new-administration-signaling-enduring-enforcement-focus/).
- **Insurance.** Verisk's 2026 general-liability exclusions for generative
  AI may leave a venture uncovered
  (https://www.independentagent.com/vu_resource/verisk-to-roll-out-new-general-liability-exclusions-for-generative-ai-exposures/).

**Not settled:** these are questions for counsel, listed under Needs Tom
in the plan:

- the state of incorporation;
- whether each venture should be its own LLC;
- tax;
- EU or California consumers.
