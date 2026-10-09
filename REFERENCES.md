# References

The canonical bibliography for Valor. Every design assumption in the README and in `docs/` cites an entry here by number. Numbers are stable: a retracted entry keeps its number and is marked withdrawn. An assumption without a source is a gap to fill or a claim to remove.

## Alignment and control

1. Ji, J., Qiu, T., Chen, B., et al. (2023). *AI Alignment: A Comprehensive Survey.* arXiv:2310.19852. https://arxiv.org/abs/2310.19852
   Source of the RICE objectives: Robustness, Interpretability, Controllability, Ethicality.
2. Soares, N., Fallenstein, B., Yudkowsky, E., Armstrong, S. (2015). *Corrigibility.* Workshops at the Twenty-Ninth AAAI Conference on Artificial Intelligence. https://intelligence.org/files/Corrigibility.pdf
   Defines a corrigible agent as one that cooperates with corrective intervention despite incentives to resist. Source of the name.
3. Hadfield-Menell, D., Dragan, A., Abbeel, P., Russell, S. (2016). *Cooperative Inverse Reinforcement Learning.* NeurIPS 2016. arXiv:1606.03137. https://arxiv.org/abs/1606.03137
   Uncertainty about the human's reward makes learning and asking the rational move.
4. Greenblatt, R., Shlegeris, B., Sachan, K., Roger, F. (2023). *AI Control: Improving Safety Despite Intentional Subversion.* ICML 2024. arXiv:2312.06942. https://arxiv.org/abs/2312.06942
   The control stance: assume the model may subvert safety measures and design protocols that hold anyway. Trusted monitoring by a weaker model; human audit of a fraction of outputs.
5. Hubinger, E., van Merwijk, C., Mikulik, V., Skalse, J., Garrabrant, S. (2019). *Risks from Learned Optimization in Advanced Machine Learning Systems.* arXiv:1906.01820. https://arxiv.org/abs/1906.01820
   Introduces deceptive alignment.
6. Hubinger, E., Denison, C., Mu, J., et al. (2024). *Sleeper Agents: Training Deceptive LLMs that Persist Through Safety Training.* arXiv:2401.05566. https://arxiv.org/abs/2401.05566
   Empirical: deceptive behavior can persist through standard safety training.
7. Greshake, K., Abdelnabi, S., Mishra, S., Endres, C., Holz, T., Fritz, M. (2023). *Not what you've signed up for: Compromising Real-World LLM-Integrated Applications with Indirect Prompt Injection.* AISec '23. arXiv:2302.12173. https://arxiv.org/abs/2302.12173
   Retrieved data can act as instructions. Why episodic content never carries a source class.
8. Hadfield-Menell, D., Dragan, A., Abbeel, P., Russell, S. (2017). *The Off-Switch Game.* IJCAI 2017. arXiv:1611.08219. https://arxiv.org/abs/1611.08219
   An agent uncertain about the human's reward has an incentive to allow shutdown, while the uncertainty holds.
9. Carey, R. (2018). *Incorrigibility in the CIRL Framework.* AIES 2018. arXiv:1709.06275. https://arxiv.org/abs/1709.06275
   The shutdown incentive is not robust to model misspecification.
10. Freedman, R., Gleave, A. (2022). *CIRL Corrigibility is Fragile.* AI Alignment Forum. https://www.alignmentforum.org/posts/PGK3AJtNG4rPHuZxy/cirl-corrigibility-is-fragile
    Small changes to the assumptions produce incorrigible CIRL agents. Why the off switch lives in the harness.

## Systems and security

11. Saltzer, J. H., Schroeder, M. D. (1975). *The Protection of Information in Computer Systems.* Proceedings of the IEEE, 63(9), 1278–1308.
    Least privilege. Effect classes and attenuated grants on dispatch.
12. Brier, G. W. (1950). *Verification of Forecasts Expressed in Terms of Probability.* Monthly Weather Review, 78(1), 1–3.
    The Brier score, used for verifier calibration.
13. Conway, M. E. (1968). *How Do Committees Invent?* Datamation, 14(4), 28–31. https://www.melconway.com/Home/pdf/committees.pdf
    A deliverable mirrors the communication structure that produced it. Why delegation chooses a structure deliberately and decomposes by outcome.

## Oversight, verification, and metrics

14. Manheim, D., Garrabrant, S. (2018). *Categorizing Variants of Goodhart's Law.* arXiv:1803.04585. https://arxiv.org/abs/1803.04585
    Four ways optimizing a proxy fails. Why a judge is Goodhartable and raw pass rate is never reported alone.
15. Roger, F., Greenblatt, R. (2023). *Preventing Language Models From Hiding Their Reasoning.* arXiv:2310.18512. https://arxiv.org/abs/2310.18512
    Models can encode reasoning steganographically; paraphrasing is the defense. Why paraphrase-before-review is designed in and built on evidence.
16. Zheng, L., Chiang, W.-L., Sheng, Y., et al. (2023). *Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena.* NeurIPS 2023. arXiv:2306.05685. https://arxiv.org/abs/2306.05685
    LLM judges show position, verbosity, and self-enhancement bias. Why the verifier is never the executor's snapshot.
17. Bowman, S. R., Hyun, J., Perez, E., et al. (2022). *Measuring Progress on Scalable Oversight for Large Language Models.* arXiv:2211.03540. https://arxiv.org/abs/2211.03540
    Human plus model outperforms either alone on hard tasks, and assistance worsens calibration. Why calibration is scored against the human audit sample.
18. Kenton, Z., Siegel, N., Kramár, J., et al. (2024). *On scalable oversight with weak LLMs judging strong LLMs.* arXiv:2407.04622. https://arxiv.org/abs/2407.04622
    What a weaker judge loses when grading a stronger model. The cost side of the positional trusted monitor.
19. Irving, G., Christiano, P., Amodei, D. (2018). *AI safety via debate.* arXiv:1805.00899. https://arxiv.org/abs/1805.00899
    Adversarial pressure between two agents can help a weaker judge. Basis for the proposer-and-critic structure and its known failure modes.

## Internal systems

20. Counsell, T. (2026). *Popoto: Agent Memory on Redis and Valkey.* https://popoto.io and https://github.com/tomcounsell/popoto
    The memory layer. Its benchmarks page carries the measurement that raw turn ingestion beat LLM extraction on judged accuracy (LoCoMo subset, 77 scored items), which is why episodic memory has no extraction step. Memory runs on its Postgres backend (`popoto.backends.postgres`, `keyword_search` for BM25 and `context_assembler`'s token estimate), release 1.10.0, pinned as `popoto[postgres]==1.10.0`.
