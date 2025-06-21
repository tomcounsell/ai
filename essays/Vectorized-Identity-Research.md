# Vectorized Identity and Personality Drift: Modeling Personal Transformation in Semantic Space

**Author:** Tom Counsell, BA CS, RPCV  
**Date:** January 2025  
**Keywords:** computational identity, semantic spaces, interdisciplinary research, experimental psychology, AI ethics

---

## Abstract

This paper explores the theoretical and empirical foundations for representing human personality and identity as navigable vectors in high-dimensional semantic space. We examine how Large Language Models (LLMs) and text embeddings can capture personality traits, track identity changes over time, and potentially guide personal transformation. Drawing from psychological theory, philosophical considerations, and computational methods, we outline experimental approaches, ethical considerations, and interdisciplinary research opportunities for developing AI-assisted personal development tools.

## Conceptual Framing of Identity as a Vector

### Personality as a Vector in High-Dimensional Space

One approach to model personality is to represent an individual's characteristics as a point in a semantic embedding space. This idea builds on the lexical hypothesis in psychology, which holds that personality traits are encoded in language. In fact, the discovery of the Big Five trait dimensions arose from analyzing the co-occurrence of personality adjectives (e.g. friendly, curious) – essentially an early form of vector analysis on language data[^1]. 

Modern language embeddings (like Word2Vec or BERT) similarly represent words and documents as high-dimensional vectors capturing semantic properties[^2]. By extension, a person's written self-description or narrative can be embedded as a vector, offering a numerical representation of their identity or personality as expressed in language. For example, recent studies have used latent semantic analysis (LSA) and other embedding techniques to infer Big Five personality traits from text, effectively locating individuals in a five-dimensional trait space derived from word vectors[^3][^4]. This suggests it is viable to map aspects of personality onto a vector space where distances and directions have meaning.

### Identity as Position and Path

Viewing identity as a point in an embedding space carries provocative theoretical implications. If each person's current state is a vector, then life experiences might nudge this vector in certain directions over time – implying a trajectory or path for one's identity. In this framing, personal growth could be seen as a semantic vector translation: a movement from a current-self vector toward a goal-self vector. 

For instance, one could attempt to capture the "direction" from an introverted writing style to an extraverted one, or from a pessimistic self-narrative to a more optimistic one, as a vector in the space. This is analogous to how word embeddings can have semantic directions (e.g., the vector difference between "king" and "queen" represents a gender change). If meaningful, the "delta vector" between Actual Self and Ideal Self might encode the semantic changes needed for personal transformation.

However, identity is complex – more like a landscape than a single point. Some psychological models suggest multiple layers or facets of personality (e.g. traits, values, narrative identity), and even multiple sub-personalities (as in Internal Family Systems, which views the psyche as composed of semi-independent "parts"). This means a single vector might be an oversimplification. A person could potentially be represented by a combination of vectors (each for different facets or subselves), or as a distribution of points rather than one fixed coordinate. These nuances highlight that while the vector model is a powerful abstraction, it should be used with caution and awareness of what it may gloss over.

### The "Ideal" Personality and Ethical Considerations

Defining an "ideal" vector for someone's personality raises important ethical and philosophical questions. In psychology, it's well-established that people carry a concept of an ideal self – the attributes they would like to possess – and a real (actual) self. Discrepancies between the two can motivate growth but also lead to distress if the gap is large[^5][^6]. Carl Rogers argued that psychological well-being comes from congruence between one's real self and ideal self; an unrealistic ideal can create perpetual dissatisfaction.

Thus, any system that sets a fixed "ideal vector" for a person must ensure that this ideal is self-defined and flexible, not an imposed norm. There is no universally optimal personality – aspiring to be one's "best self" should not mean converging on one stereotypical embedding. Ethically, using AI to guide personal change requires respecting autonomy and authenticity. Autonomy means the individual should steer the direction of change (the AI should assist reflection, not dictate who to become). Authenticity means the process should help the person become more truly themselves (by their own values), rather than pressuring them to adopt arbitrary traits for external approval.

Philosophically, if we treat identity as a point in a predefined space, we risk reifying a person as just data. It's crucial to remember that identity is also a narrative and an ongoing process of becoming, not just a static coordinate. Any "ideal" in the vector model might better be thought of as a moving target – evolving as the person grows – rather than a fixed endpoint.

## Experimental Viability and First Steps

### Small-Scale Experiments

Even as a solo researcher, there are plausible ways to probe this vectorized identity concept. One minimal experiment is to collect textual self-descriptions at different points in time and analyze them with off-the-shelf embedding models. For example, you could write a one-paragraph narrative about your current self and another describing your ideal future self. By embedding these two texts (using a model like OpenAI's text embeddings or BERT), you obtain two vectors. The difference between these vectors is a candidate "growth direction."

You could then test if intermediate points along this vector produce narratives that subjectively feel like gradual transformations from the current to ideal self (perhaps by using an LLM to generate a narrative given a point between the two vectors). Another experiment is to take historical personal data – say journal entries or social media posts over years – and embed each entry. This yields a time series of identity vectors. By plotting or comparing these, you can detect semantic drift in your own self-expression.

Do the embeddings show a clear trend or shift (for instance, moving closer to the vector of a more confident tone or different interests)? Such drift could be quantified by measuring the cosine similarity of writings to certain target descriptors over time. A concrete example might be tracking whether one's writing moves closer to the semantic cluster of "optimistic" language after practicing positive reflection exercises.

### Input Data for Personality Embeddings

The choice of data to represent personality is crucial. Textual data is a rich source – this could include personal essays, diary entries, interview transcripts, social media content, or responses to open-ended questions like "Describe yourself." Language use can reveal a great deal about personality (e.g., use of first-person pronouns, emotional words, complex syntax, etc., have known correlations to traits and states[^1][^3]).

Another input form is assessment-based data: for instance, questionnaire results (like Big Five inventory scores) could be embedded by encoding them as a text profile or directly as a numerical vector. While a 5-dimensional trait score is itself a simple vector, one could feed those traits into an LLM to generate a richer descriptive paragraph which is then embedded in a larger semantic space, combining quantitative assessment with narrative.

Behavioral data might also be considered – e.g. logs of activities, choices, or physiological data – but those are not naturally in a semantic form. To use them, one might translate behaviors into textual summaries ("This week, the person socialized on 3 days and read 2 books…") before embedding. In short, any data reflecting a person's characteristics can be transformed into a textual or vector representation. The most accessible approach is to use text that the person themselves produces, since it inherently carries their voice and perspective.

### Measuring Semantic "Drift" Over Time

A key technical challenge is detecting meaningful change in personality vectors over time. This is analogous to concept drift in machine learning – here, the "concept" is the person's identity. One straightforward measure is distance in embedding space: if a person's vector at time A is far from time B, some change has occurred. However, distance alone doesn't tell us what changed.

For insight, we can examine the direction of change by subtracting the earlier vector from the later vector. Does this difference vector align with any known semantic directions? For example, if it aligns with a "more positive affect" direction or a "more conscientious" direction (which could be determined by comparing with reference vectors for those traits[^7]), that suggests the nature of the drift.

Another approach is to track specific dimensions or cluster affiliations: many embedding models allow projection onto axes (for instance, a happiness-sadness axis, or an introversion-extroversion axis derived from word lists[^7]). If over time the projection score on an "extroversion" axis increases, one might say the person's expressed personality became more extroverted.

It's important that these measurements be psychologically valid. They should be checked against external data if possible – for instance, did a known life event or conscious effort correspond to the vector changes observed? If someone undergoes therapy or a major life transition and we see the embedding shifting in a direction corresponding to increased emotional stability, that lends credence to the method.

Additionally, validating with self-report changes (e.g., the person's Big Five scores before and after, or qualitative reports of feeling different) would be ideal. Keep in mind that short-term fluctuations in mood or context could also cause semantic shifts in writing that don't represent a lasting personality change. So, distinguishing between momentary state changes and longer-term trait development is part of the challenge (one might do this by averaging embeddings over longer periods, or filtering out context-specific content in the text).

### Feasibility and Path to a Live Product

While the above experiments can be done on a small scale, moving toward a real product (e.g., an "AI coach" for personal growth) will require robust techniques and ethical safeguards. Technically, we'd need to ensure the embeddings reliably capture aspects of personality and that the LLM guidance grounded in those embeddings genuinely helps people.

Early prototypes might involve a chatbot that periodically asks the user reflective questions, embeds their responses, and detects if they are moving toward their stated goals or values. For example, if a user's goal vector indicates "more creative and open" and their recent writings remain very factual and routine, the system might gently prompt more imaginative exercises – effectively nudging the user along the semantic direction of openness.

Such a system would have to be tested in controlled trials with real users, measuring outcomes like user satisfaction, perceived self-improvement, or even changes in standard psychological assessments. The feasibility is tempered by many unknowns (e.g., will users trust and engage with such an AI, can it avoid manipulation or bias, can it adapt to each person's unique ideal?). Therefore, initial live testing should likely be done in partnership with psychologists or coaches, to monitor for any adverse effects and to iterate on the guidance strategies.

It may be a long road, but even simple prototypes (like a journaling app enhanced with semantic feedback) could be a stepping stone. Ultimately, the concept's viability hinges on whether these vector representations truly map to the rich reality of human identity – a hypothesis that these early experiments will help clarify.

## Interdisciplinary Perspectives and Theoretical Foundations

### Psychological Theories – Narrative and Traits

Any attempt to computationally model identity transformation should draw on established psychological frameworks. Narrative identity theory is especially relevant: it posits that people make sense of their lives by forming an internalized life story that links past, present, and future into a coherent narrative[^8]. This evolving story provides unity and purpose, and is considered a fundamental layer of personality (distinct from, say, basic traits). Changes in how one narrates their life (finding new meaning in past events, redefining one's future aspirations) are a core form of personal transformation in therapy and development[^8][^9].

An AI system aiming to guide personality change could leverage this by helping users reframe their narratives. For instance, Large Language Models might assist users in writing new chapters of their life story or imagining alternate storylines, effectively moving them in narrative space. This is not far from practices in narrative therapy, where clients are encouraged to rewrite the narrative of their challenges to find growth-oriented meanings.

On the other hand, trait theory (like the Big Five) offers a more structural view: personality as a set of dimensions (e.g. extraversion, neuroticism). Trait research shows that while personality traits are fairly stable, they do undergo systematic change across the lifespan (people tend to become more emotionally stable and agreeable with age, for example[^10]). Moreover, significant life events can prompt trait changes in some individuals.

So a semantic vector model might track these trait-like shifts – say, a vector slowly moving in the direction of greater calmness or sociability as one ages or after a life transition. Indeed, evidence is emerging that LLMs can capture something like these traits; for example, one study found that large language models "rediscover" the Big Five traits within their internal representations[^11]. It's conceivable to project a person's text embedding onto such latent trait vectors to gauge their personality at a given time[^12].

Another psychological model, Internal Family Systems (IFS), challenges the notion of a singular identity: it suggests that our mind contains multiple sub-personalities ("parts"), each with its own perspective and goals. Personal growth in IFS involves harmonizing these parts under the leadership of one's core Self. For our vector model, this could mean that what we call "the identity vector" might actually be a composite of multiple vectors (each part could be represented by a different cluster of semantic content). Changes might involve certain parts "quieting down" and others becoming more prominent in the person's self-narrative.

While complex, this hints that a truly comprehensive model might need to detect distinct voices or themes in text that correspond to different parts of the psyche – a task an LLM might assist with by, say, identifying contradictory tones or viewpoints in a journal entry.

### Philosophical Notions – Selfhood, Autonomy, Authenticity

The idea of moving through a "self space" raises age-old philosophical questions: What is the self, and can it be quantified or reified? Philosophers have variously argued that the self is an illusion, a bundle of perceptions (Hume), or a constantly constructed narrative (existentialists, narrative philosophers). If identity is not a static entity but a continuous process, any snapshot vector will fail to capture its fluidity. One might reconcile this by emphasizing the trajectory over the static position – it's the movement through the space that represents the living process of selfhood.

Autonomy is another critical concept: the capacity to choose and direct one's own path. In using AI to guide personal transformation, we must ensure the individual's agency is primary. The role of the LLM should be akin to a compass or a mirror, not a dictator of direction. The user defines their ideal (or perhaps explores it interactively with the AI), and the AI offers insights or suggestions aligned to that user-defined vector. Maintaining autonomy also means the user can change their goals – the "ideal vector" is not immutable.

Authenticity relates to being true to oneself. There's a potential paradox: if an AI is guiding you, are you still you? To address this, the AI should be framed as enhancing self-reflection, not replacing it. In fact, the AI might prompt deeper inquiry into what the user really values or enjoys, helping them articulate an ideal that feels authentic to them. The process should encourage reflection like, "Is this goal truly mine or something I think I 'should' do?"

Philosophically, we must avoid treating the ideal as a simple optimization target (as if maximizing certain traits would yield a perfect person). Humans are not mere points to be moved to a more desirable coordinate; we are meaning-makers. Thus, the concept of semantic direction toward growth is best grounded in personal meaning: moving towards a self that the person finds more meaningful, fulfilled, and aligned with their values. This is congruent with ideas in existential psychology (e.g., Kierkegaard's or Frankl's emphasis on choosing or finding meaning).

Practically, involving ethicists or philosophers in the project could help anticipate pitfalls – such as the risk of homogenizing personalities or the implications of "designing" oneself with AI help.

### Relevant Precedents in Therapy and Education

Although the framing of "identity vectors" is novel, there are precedents that resonate with parts of the idea. In therapeutic contexts, narrative therapy (White & Epston) encourages rewriting one's narrative to change one's relationship to problems – essentially a guided transformation of identity through language. Cognitive-behavioral techniques involve identifying current thought patterns and practicing new, more adaptive thoughts – one could see this as moving in the "cognitive style" subspace (for example, from a pessimistic explanatory style toward an optimistic one).

The "Best Possible Self" exercise in positive psychology is a concrete example of using imagined ideals for growth: individuals spend time writing about their ideal future self, which has been shown to increase optimism and well-being[^13][^14]. This exercise essentially has people articulate an aspirational identity and reflect on how to bridge the gap, very much in spirit with defining an ideal point and working toward it.

In education and coaching, there are programs for "self-authoring" or life design (such as certain college programs or online courses) where people write about their values, their future plans, and how to evolve – these too are about intentional identity development. Our concept could augment such programs by analytically identifying themes or gaps in someone's narrative compared to their stated goals.

Even in literature and art, the notion of identity transformation is fundamental – the archetypal "hero's journey" is about a character leaving their comfort zone and fundamentally changing who they are. One could imagine analyzing fictional characters' dialogues via embeddings to illustrate clear semantic shifts from the story's start to end, as a proxy for understanding real personal growth.

Lastly, the emerging field of digital mental health and AI coaching provides early prototypes of using conversational agents for self-improvement. For example, some AI chatbots already engage in motivational interviewing or CBT-style dialogues. Our approach would take this a step further by maintaining a vector representation of the user's identity and progress.

Notably, a 2024 article in Psychology Today described how LLM dialogues can serve as a "mirror" to the self, reflecting a user's thoughts and patterns back in a way that fosters insight[^15]. This supports the idea that an LLM could help externalize one's current self-narrative and even project forward – essentially plotting points in that identity space in an interactive fashion.

## Research Framing and Collaborative Exploration

### Framing to Attract Cross-Disciplinary Interest

To engage researchers from psychology, philosophy, AI, and cognitive science, this problem should be framed in a way that speaks to each domain's questions. One effective framing is as an integration of subjective, narrative aspects of identity with objective, computational modeling. Emphasize that this research does not claim to reduce identity to numbers, but rather to create a bridge between humanistic understanding of personal growth and the powerful pattern recognition of AI.

For psychologists, it can be pitched as a new tool to measure and facilitate changes that until now have been qualitative – a way to quantify narrative identity or track therapy progress in between sessions, for example. For AI researchers, the problem can be framed as pushing the boundaries of representation learning: can we create embeddings that capture something as elusive as "selfhood" and its evolution? It's a grand challenge blending NLP, time-series modeling, and perhaps reinforcement learning (if the AI is to actively help someone achieve a target state).

Cognitive scientists would be intrigued by what this implies about how humans mentally represent self-concept – perhaps our brains themselves have something like a vector encoding for concepts of self and others, which this work could shed light on. Philosophers and ethicists would be drawn in by the questions of personhood, autonomy, and the impact of technology on self-development, ensuring the project remains critically self-aware.

To provoke insightful responses, one might frame provocative questions such as: "Can an AI map the path of your becoming?" or "Is personal growth just a journey in semantic space, and if so, who charts the path – you or the algorithm?". Presenting preliminary findings or hypotheses (e.g., "We found a consistent semantic vector that differentiates participants' narratives before vs. after a major life event") would spark debate on interpretability (what does that vector mean?) and validity.

It may also help to connect with existing interdisciplinary initiatives. For instance, the concept touches on computational psychometrics (using AI to assess psychological traits[^17][^18]) and AI in mental health (using LLMs for therapy support). Framing the work as a natural convergence of these areas can attract researchers who have been working on parallel tracks. Hosting a workshop or special session on "Computational Models of Personal Identity and Growth" at a conference could bring together diverse experts – psychologists with data on life stories, NLP researchers with language models, and philosophers of mind.

### Datasets, Tools, and Collaborative Structures

As this is a multifaceted problem, a collaborative approach is valuable. Different types of datasets would be useful:

- **Longitudinal personal narratives** (e.g., collections of journal entries, blogs or social media posts from the same individuals over years) could allow modeling of how individuals change. For example, the "myPersonality" dataset (Facebook posts with personality scores) or archival diaries could be mined. If none are readily available, one could start collecting data via a custom app where users periodically write reflections and take personality surveys, with full consent for research use.

- **Therapeutic transcripts** (anonymized) might show how a person's way of talking about themselves shifts from the start of therapy to later sessions – a rich source for semantic change tied to known interventions.

- **Fictional character arcs** (as mentioned) or biographies could be a proxy dataset to test algorithms (since those often have clear "before and after" states in a person's life).

- **Standard psychology datasets**, like the EAR (Electronically Activated Recorder) corpus or forums where people discuss their problems and later post outcomes, could also be repurposed to see if language embeddings predict personal outcomes.

On the LLM side, one might fine-tune models on self-narratives or even do reinforcement learning where the reward is aligned with moving closer to an aspirational description. Collaborators in NLP could help ensure the embeddings and models are state-of-the-art and interpretability methods are applied (to avoid black-box advice). Psychologists can contribute validated measures for outcomes (so we're not just guessing that a vector change is good – we can correlate it with, say, improved well-being scores or goal attainment). Philosophers/ethicists can be part of an advisory board to oversee the project's direction and its impact on participants.

A concrete collaborative structure might be a research consortium or lab that runs an ongoing study with volunteers who want to engage in AI-assisted personal development. Participants could use a tool (maybe a journaling chatbot) over months, and the team analyzes the data from multiple angles: quantitative vector shifts, qualitative analysis of narrative themes, psychological assessments, etc. Regular meetings between the technical team and the psychological team would ensure interpretations make sense in both domains.

Given the breadth of this concept, publications could emerge in venues ranging from machine learning conferences (e.g. demonstrating a new technique for embedding personal narratives) to psychology journals (e.g. showing how narrative coherence or other identity metrics change with an intervention, measured via embeddings[^16]).

In framing this for maximal impact, it may help to articulate a compelling vision: for example, "Imagine if we could chart a map of a person's identity, and along with them, identify a path on that map toward who they wish to become. Our research seeks to lay the groundwork for such maps, using the latest AI semantics combined with deep psychological insight." This kind of vision can excite funding agencies and collaborators, as it presents a novel synthesis of humanistic aspiration and scientific innovation.

It acknowledges that the journey is long – we're not claiming to have a ready-made product, but a direction of inquiry that could transform how we think about personal development. By clearly stating the open questions (as we have: representation, interpretation, ethical use, etc.), we invite experts from various fields to weigh in where their knowledge is critical.

### Provoking Rigorous Discourse

To get the most insightful feedback, one strategy is to publish a conceptual paper or essay in an interdisciplinary journal or archive (even as a preprint or blog) outlining this idea of vectorized identity and calling out the challenges. Sometimes just posing a bold hypothesis will bring out constructive criticism or "here's how you could test that" suggestions from others.

For instance, a philosopher might respond with a critique about reductionism – which could lead to a more robust framework that addresses that critique (maybe by incorporating multi-vector models of self as mentioned). A psychologist might point out a particular theory of personality development (like self-determination theory or growth mindset research) that could be integrated, thus enriching the model.

Essentially, engaging the community through open discussion, maybe an online forum or a symposium, can refine the approach. In summary, framing this research as a fusion of AI-driven semantic modeling with personal narrative and growth will naturally draw a diverse crowd. The key is to remain open-ended and exploratory in the framing, acknowledging the depth of the questions. By doing so, you signal that this is not a solved problem but an open frontier – one that will benefit from many minds and disciplines working together.

With careful framing and collaborative effort, the idea of modeling and guiding personal transformation via semantic direction can evolve from a speculative notion into a concrete research program, and eventually, if proven effective and ethical, into real tools for helping people chart their journeys of self-improvement.

## Conclusion

Modeling human personality as a navigable vector in semantic space is an ambitious undertaking, sitting at the crossroads of technology and the very personal realm of identity. We have explored how personality might be encoded in vectors derived from language, how the delta between one's current and ideal self could be conceptualized as a direction for growth, and how large language models might serve as guides or mirrors along this journey.

There are encouraging signs – language embeddings do capture psychologically meaningful patterns[^19][^20], and people's narratives can indeed change in measurable ways as they grow. At the same time, we've acknowledged the deep theoretical challenges and ethical responsibilities: identity is multi-faceted and dynamic; any "ideal" must respect individual values and autonomy.

Moving forward, the feasibility of this idea can be tested in small experiments with personal data, expanded through interdisciplinary research, and carefully scaled up towards applications in coaching or therapy with real human users. The ultimate aim is not to let an AI dictate who someone should be, but to empower individuals with new insights – a kind of semantic compass – in their journey of becoming.

By combining the strengths of psychological theory, philosophical reflection, and AI modeling, this line of research holds the promise of illuminating that journey in unprecedented ways, while always keeping the human in the loop as the author of their own story.

## References

[^1]: Cutler, A. (2022). "The Big Five are word vectors." *Vectors of Mind*. https://www.vectorsofmind.com/p/the-big-five-are-word-vectors

[^2]: Mikolov, T., et al. (2013). "Efficient estimation of word representations in vector space." *arXiv preprint arXiv:1301.3781*.

[^3]: Jorge-Botana, G., et al. (2023). "Modeling personality language use with small semantic vector subspaces." *Personality and Individual Differences*, 205, 112514.

[^4]: Yarkoni, T. (2010). "Personality in 100,000 words: A large-scale analysis of personality and word use among bloggers." *Journal of Research in Personality*, 44(3), 363-373.

[^5]: Higgins, E. T. (1987). "Self-discrepancy: A theory relating self and affect." *Psychological Review*, 94(3), 319-340.

[^6]: Wikipedia. "Self-discrepancy theory." https://en.wikipedia.org/wiki/Self-discrepancy_theory

[^7]: Holtzman, N. S., et al. (2019). "Assessing the Big Five personality traits with latent semantic analysis." *ResearchGate*. https://www.researchgate.net/publication/305843228_Assessing_the_Big_Five_personality_traits_with_latent_semantic_analysis

[^8]: McAdams, D. P. (2001). "The psychology of life stories." *Review of General Psychology*, 5(2), 100-122.

[^9]: Wikipedia. "Narrative identity." https://en.wikipedia.org/wiki/Narrative_identity

[^10]: Roberts, B. W., & Mroczek, D. (2008). "Personality trait change in adulthood." *Current Directions in Psychological Science*, 17(1), 31-35.

[^11]: Serapio-García, G., et al. (2024). "Rediscovering the Latent Dimensions of Personality with Large Language Models." *arXiv preprint arXiv:2409.09905v1*.

[^12]: Jorge-Botana, G., et al. (2023). "Modeling personality language use with small semantic vector subspaces." *Personality and Individual Differences*, 205, 112514.

[^13]: King, L. A. (2001). "The health benefits of writing about life goals." *Personality and Social Psychology Bulletin*, 27(7), 798-807.

[^14]: Sheldon, K. M., & Lyubomirsky, S. (2006). "How to increase and sustain positive emotion: The effects of expressing gratitude and visualizing best possible selves." *The Journal of Positive Psychology*, 1(2), 73-82.

[^15]: Nosta, J. (2024). "AI as a Mirror Into the Self." *Psychology Today*. https://www.psychologytoday.com/us/blog/the-digital-self/202409/ai-as-a-mirror-into-the-self

[^16]: Publications could emerge in venues ranging from machine learning conferences to psychology journals, demonstrating interdisciplinary impact of personality vectoring research.

[^17]: Kosinski, M., et al. (2013). "Private traits and attributes are predictable from digital records of human behavior." *Proceedings of the National Academy of Sciences*, 110(15), 5802-5805.

[^18]: Bleidorn, W., & Hopwood, C. J. (2019). "Using machine learning to advance personality assessment and theory." *Personality and Social Psychology Review*, 23(2), 190-203.

[^19]: Lee, S., et al. (2024). "Large Language Models and Text Embeddings for Detecting Depression and Suicide in Patient Narratives." *JAMA Network Open*, 7(11), e2443919.

[^20]: Tausczik, Y. R., & Pennebaker, J. W. (2010). "The psychological meaning of words: LIWC and computerized text analysis methods." *Journal of Language and Social Psychology*, 29(1), 24-54.