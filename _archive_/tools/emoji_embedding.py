"""Emoji embedding index for fast reaction selection.

Maps the validated Telegram reaction emojis to descriptive feeling-word
embeddings via cosine similarity.

This index covers STANDARD emoji only. Custom (Premium) emoji reactions are
still supported by the transport — ``EmojiResult.document_id`` and
``set_reaction``'s ``ReactionCustomEmoji`` branch are live — but their ids are
pinned statically by the caller rather than searched semantically (#2716).

Standard emoji cache: data/emoji_embeddings.json

Usage:
    from tools.emoji_embedding import find_best_emoji, find_best_emoji_for_message, EmojiResult

    result = find_best_emoji("excited")        # -> EmojiResult
    str(result)                                 # -> "🔥" (backward compatible)
    result.is_custom                            # -> False for standard emoji

    result = find_best_emoji_for_message(text)  # -> EmojiResult
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

OPENROUTER_EMBEDDINGS_URL = "https://openrouter.ai/api/v1/embeddings"
EMBEDDING_MODEL = "openai/text-embedding-3-small"


def _compute_embedding(text: str, api_key: str) -> list[float] | None:
    """Compute embedding for text using OpenRouter."""
    import requests

    try:
        response = requests.post(
            OPENROUTER_EMBEDDINGS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": EMBEDDING_MODEL,
                "input": text[:8000],
            },
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("data", [{}])[0].get("embedding")
    except Exception:
        return None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0

    dot_product = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)


# Cache location for pre-computed emoji embeddings
CACHE_PATH = Path(__file__).parent.parent / "data" / "emoji_embeddings.json"

# Default fallback emoji (thinking face)
DEFAULT_EMOJI = "\U0001f914"  # 🤔

# Emojis that must never be selected as a reaction — the single source of truth
# for "never aim hostility at a user." Every reaction this system sets lands on a
# user's own message, so any outward-directed hostile face reads as blame at the
# person who messaged us. These are filtered out of find_best_emoji candidates at
# selection time regardless of what a stale on-disk cache contains. They stay in
# VALIDATED_REACTIONS (they are valid Telegram reactions); they are simply
# unselectable by the resolver. Self-directed sadness/worry (😢 😭 😨) is NOT
# blocked — it expresses empathy, not hostility.
BLOCKED_REACTION_EMOJIS = frozenset(
    {
        "\U0001f595",  # 🖕 middle finger
        "\U0001f44e",  # 👎 thumbs down (dismissive)
        "\U0001f92c",  # 🤬 face with symbols on mouth (swearing)
        "\U0001f621",  # 😡 pouting face (anger)
        "\U0001f92e",  # 🤮 face vomiting (disgust)
        "\U0001f631",  # 😱 face screaming in fear (outward-directed shock/blame)
    }
)

# Placeholder character used for custom emoji in message text
CUSTOM_EMOJI_PLACEHOLDER = "\u2753"  # ❓ (replaced by entity rendering)

# Softmax temperature for emoji selection: higher = more random (flatter distribution).
# At 1.0 the distribution tracks score differences closely; at 5.0+ it's nearly uniform.
REACTION_TEMPERATURE = 4.0

# Number of top candidates to sample from (emoji variety window).
REACTION_TOP_K = 3


@dataclass
class EmojiResult:
    """Result from emoji embedding lookup.

    Carries both standard emoji string and optional custom emoji document_id.
    Provides ``__str__`` for backward compatibility -- callers that only need
    a string representation continue to work unchanged.

    Attributes:
        emoji: Standard Unicode emoji string, or None if only custom matched.
        document_id: Telegram custom emoji document ID (int64), or None.
        is_custom: True when the result is a custom emoji.
        score: Cosine similarity score of the best match.
    """

    emoji: str | None = None
    document_id: int | None = None
    is_custom: bool = False
    score: float = 0.0

    def __str__(self) -> str:
        """Return the emoji string for display.

        For standard emoji, returns the Unicode character.
        For custom emoji, returns the placeholder character.
        Falls back to the default thinking emoji if neither is set.
        """
        if self.emoji:
            return self.emoji
        if self.is_custom:
            return CUSTOM_EMOJI_PLACEHOLDER
        return DEFAULT_EMOJI

    @property
    def display(self) -> str:
        """Alias for str(self)."""
        return str(self)


# Descriptive labels for each of the validated Telegram reaction emojis.
# These labels are embedded and compared against input text via cosine similarity.
# fmt: off
EMOJI_LABELS: dict[str, str] = {
    # Hearts/love
    "\u2764": "love, affection, warmth, caring, heart",
    "\u2764\u200d\U0001f525": "passionate love, burning desire, intense feelings, fire heart",
    "\U0001f494": "heartbreak, sadness, loss, broken heart, disappointment",
    "\U0001f498": "romantic, crush, attraction, cupid, falling in love",
    "\U0001f60d": "adoring, smitten, heart eyes, beautiful, gorgeous",
    "\U0001f970": "affectionate, warm, tender, sweet, caring smile",
    "\U0001f618": "kiss, flirty, sending love, mwah, blowing kiss",
    "\U0001f48b": "kiss mark, lipstick, smooch, romantic gesture",
    # Hands
    "\U0001f44d": "good, agree, approve, thumbs up, yes, okay, nice",
    "\U0001f44e": "bad, disagree, dislike, thumbs down, no, reject",
    "\U0001f44f": "applause, clapping, bravo, well done, congratulations",
    "\U0001f64f": "please, thank you, grateful, prayer, hope, namaste",
    "\U0001f44c": "perfect, okay, fine, precise, excellent, on point",
    "\U0001f91d": "agreement, deal, handshake, partnership, cooperation",
    "\u270d": "writing, noting, composing, drafting, penning",
    # Positive faces
    "\U0001f601": "happy, grinning, joyful, cheerful, beaming smile",
    "\U0001f923": "hilarious, laughing hard, rolling on floor, so funny, comedy",
    "\U0001f929": "amazing, starstruck, impressed, dazzled, wow, star eyes",
    "\U0001f607": "innocent, angelic, pure, wholesome, blessed, good",
    "\U0001f60e": "cool, confident, sunglasses, chill, relaxed, smooth",
    "\U0001f913": "nerdy, geeky, smart, studious, intellectual, technical",
    "\U0001f917": "hugging, warm, welcoming, embrace, comfort, supportive",
    "\U0001fae1": "salute, respect, honor, acknowledgment, military, roger",
    # Negative faces
    "\U0001f631": "scared, shocked, horrified, screaming, terrified, alarming",
    "\U0001f92f": "mind blown, astonished, exploding head, unbelievable, incredible",
    "\U0001f92c": "angry, furious, swearing, rage, cursing, mad",
    "\U0001f622": "crying, sad, tear, upset, emotional, sorrowful",
    "\U0001f62d": "sobbing, wailing, very sad, devastated, bawling",
    "\U0001f92e": "disgusting, gross, vomit, nauseating, repulsive, sick",
    "\U0001f628": "fearful, anxious, worried, nervous, frightened",
    "\U0001f621": "angry face, mad, furious, enraged, hostile",
    # Neutral/other faces
    "\U0001f914": "thinking, pondering, considering, hmm, contemplating",
    "\U0001f971": "bored, yawning, tired, sleepy, uninterested, dull",
    "\U0001f974": "dizzy, woozy, confused, disoriented, drunk",
    "\U0001f634": "sleeping, zzz, asleep, napping, rest, tired",
    "\U0001f610": "neutral, expressionless, meh, indifferent, blank",
    "\U0001f928": "skeptical, suspicious, raised eyebrow, doubtful, questioning",
    "\U0001f92a": "crazy, silly, zany, goofy, wild, wacky",
    # Characters
    "\U0001f921": "clown, joke, funny, ridiculous, absurd, foolish",
    "\U0001f47b": "ghost, spooky, haunted, boo, halloween, supernatural",
    "\U0001f47e": "alien, space invader, game, robot, tech, digital",
    "\U0001f608": "mischievous, devil, naughty, evil grin, playfully bad",
    "\U0001f4a9": "poop, crap, garbage, terrible, awful, worthless",
    "\U0001f385": "santa, christmas, holiday, festive, gift, jolly",
    "\U0001f468\u200d\U0001f4bb": "developer, coding, programming, hacker, tech work, engineering",
    # Animals/nature
    "\U0001f54a": "peace, dove, freedom, calm, tranquil, harmony",
    "\U0001f433": "whale, ocean, big, marine, deep, vast",
    "\U0001f984": "unicorn, magical, fantasy, special, unique, rare",
    "\U0001f648": "see no evil, embarrassed, hiding, oops, covering eyes",
    "\U0001f649": "hear no evil, not listening, la la la, ignoring",
    "\U0001f64a": "speak no evil, keeping quiet, secret, shush, silent",
    # Objects/symbols
    "\U0001f525": "fire, hot, trending, lit, exciting, impressive, awesome",
    "\u26a1": "lightning, fast, quick, electric, energy, power, speed",
    "\U0001f4af": "perfect score, hundred, fully agree, absolutely, totally",
    "\U0001f3c6": "trophy, winner, champion, achievement, victory, success",
    "\U0001f389": "celebration, party, congratulations, hooray, festive",
    "\U0001f383": "halloween, pumpkin, spooky, october, scary",
    "\U0001f384": "christmas tree, holiday, festive, december, decoration",
    "\u2603": "snowman, winter, cold, snow, freezing, chilly",
    "\U0001f5ff": "moai, stone face, serious, deadpan, stoic, unimpressed",
    "\U0001f48a": "medicine, pill, cure, fix, remedy, solution, health",
    "\U0001f192": "cool button, awesome, nice, rad, sick",
    # Food
    "\U0001f34c": "banana, fruit, silly, innuendo, yellow",
    "\U0001f353": "strawberry, sweet, berry, cute, delicious",
    "\U0001f32d": "hot dog, food, casual, snack, americana",
    "\U0001f37e": "champagne, celebration, toast, cheers, party, bubbly",
    # Other
    "\U0001f31a": "new moon face, mysterious, dark, creepy, ominous",
    "\U0001f485": "nail polish, sassy, fabulous, unbothered, glamorous, diva",
    "\U0001f440": "eyes, looking, watching, paying attention, noticing, observing",
    "\U0001f937": "shrug, dunno, whatever, uncertain, who knows",
    "\U0001f937\u200d\u2642": "male shrug, dunno, whatever, uncertain, who knows",
    "\U0001f937\u200d\u2640": "female shrug, dunno, whatever, uncertain, who knows",
}
# fmt: on

# In-memory cache of embeddings (loaded lazily)
_embedding_cache: dict[str, list[float]] | None = None


def _load_or_compute_embeddings() -> dict[str, list[float]]:
    """Load emoji embeddings from cache or compute via OpenRouter API.

    Returns a dict mapping emoji -> embedding vector.
    On failure, returns an empty dict (callers fall back to default emoji).
    """
    global _embedding_cache

    if _embedding_cache is not None:
        return _embedding_cache

    # Try loading from disk cache
    if CACHE_PATH.exists():
        try:
            data = json.loads(CACHE_PATH.read_text())
            if isinstance(data, dict) and len(data) > 0:
                _embedding_cache = data
                logger.info(f"Loaded emoji embeddings from cache ({len(data)} entries)")
                return _embedding_cache
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Emoji embedding cache corrupted, rebuilding: {e}")

    # Compute embeddings via OpenRouter
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning("OPENROUTER_API_KEY not set, emoji embeddings unavailable")
        _embedding_cache = {}
        return _embedding_cache

    logger.info(f"Computing emoji embeddings for {len(EMOJI_LABELS)} emojis...")
    embeddings: dict[str, list[float]] = {}

    for emoji, label in EMOJI_LABELS.items():
        embedding = _compute_embedding(label, api_key)
        if embedding:
            embeddings[emoji] = embedding
        else:
            logger.warning(f"Failed to compute embedding for {emoji} ({label})")

    if embeddings:
        # Save to disk cache
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(json.dumps(embeddings))
            logger.info(f"Saved emoji embeddings to cache ({len(embeddings)} entries)")
        except OSError as e:
            logger.warning(f"Failed to save emoji embedding cache: {e}")

    _embedding_cache = embeddings
    return _embedding_cache


def _softmax_sample(candidates: list[tuple[str, float]], temperature: float) -> tuple[str, float]:
    """Sample an emoji from candidates using softmax-weighted probability.

    A higher temperature flattens the distribution, giving lower-ranked
    candidates a real chance. Returns (emoji, score) of the selected candidate.
    """
    if not candidates:
        return DEFAULT_EMOJI, 0.0
    if len(candidates) == 1:
        return candidates[0]

    raw = [score / temperature for _, score in candidates]
    max_raw = max(raw)
    weights = [math.exp(s - max_raw) for s in raw]
    total = sum(weights)
    weights = [w / total for w in weights]

    r = random.random()
    cumulative = 0.0
    for (emoji, score), weight in zip(candidates, weights):
        cumulative += weight
        if r <= cumulative:
            return emoji, score
    return candidates[-1]


def find_best_emoji(feeling: str) -> EmojiResult:
    """Find the best reaction emoji for a given feeling word.

    Embeds the feeling text and finds the nearest emoji by cosine similarity
    over the standard, Telegram-validated reaction set.

    This function never returns a custom emoji. The embedding index that once
    fed that branch was deleted in #2716: it had no production caller, could
    not work as written (it read ``result.documents`` from a request that
    returns only set descriptors), and never produced a cache file. Custom
    emoji reactions are still supported end to end — they now ride statically
    pinned document ids, not a semantic search. See
    ``bridge/response.py::heartbeat_reaction``.

    Args:
        feeling: A word or phrase describing the desired reaction
                 (e.g., "excited", "sad", "great work").

    Returns:
        An EmojiResult. Use ``str(result)`` for backward-compatible emoji string.
        Returns default thinking emoji EmojiResult on failure.
    """
    default_result = EmojiResult(emoji=DEFAULT_EMOJI)

    if not feeling or not isinstance(feeling, str) or not feeling.strip():
        return default_result

    embeddings = _load_or_compute_embeddings()
    if not embeddings:
        return default_result

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return default_result

    start = time.time()
    query_embedding = _compute_embedding(feeling.strip(), api_key)
    if not query_embedding:
        return default_result

    # Score all standard emoji and collect top-K candidates for sampling
    scored: list[tuple[str, float]] = []
    for emoji, emb in embeddings.items():
        if emoji in BLOCKED_REACTION_EMOJIS:
            continue
        score = _cosine_similarity(query_embedding, emb)
        scored.append((emoji, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    top_standard = scored[: max(1, REACTION_TOP_K)]
    best_standard_emoji, best_standard_score = _softmax_sample(top_standard, REACTION_TEMPERATURE)

    elapsed_ms = (time.time() - start) * 1000
    result = EmojiResult(
        emoji=best_standard_emoji,
        is_custom=False,
        score=best_standard_score,
    )
    logger.debug(
        f"find_best_emoji({feeling!r}) -> {best_standard_emoji} "
        f"(score={best_standard_score:.3f}, {elapsed_ms:.1f}ms)"
    )

    return result


# Maps work type labels (from issue/task classification) to action intent categories.
# Used by find_best_emoji_for_message to select the appropriate emoji candidates.
WORKTYPE_TO_ACTION: dict[str, str] = {
    "bug": "investigate_bug",
    "feature": "acknowledge_task",
    "chore": "acknowledge_task",
    "sdlc": "acknowledge_task",
}

# Emoji candidates per action intent. All entries must be in VALIDATED_REACTIONS.
# Categories:
#   investigate_bug  -- on it / debugging
#   problem_solving  -- working it / here to help
#   acknowledge_task -- salute / will do
#   receive_praise   -- grateful / love / trophy
#   answer_question  -- thinking (ONLY category with 🤔) / here to help
#   general          -- distinct neutral fallback
ACTION_EMOJI_MAP: dict[str, list[str]] = {
    "investigate_bug": ["👨‍💻", "👀"],
    "problem_solving": ["👨‍💻", "🤝"],
    "acknowledge_task": ["🫡", "👍"],
    "receive_praise": ["🙏", "❤", "🏆"],
    "answer_question": ["🤔", "🤝"],
    "general": ["👀"],
}


def find_best_emoji_for_message(text: str, work_type: str | None = None) -> EmojiResult:
    """Find the best reaction emoji for a message based on action intent.

    Selects an emoji from a pre-defined set of candidates for the given work_type,
    rather than computing embeddings. This is synchronous and requires no API calls.

    Args:
        text: The message text to select a reaction for.
        work_type: Optional work type label (e.g. "bug", "feature", "chore", "sdlc").
                   Maps to an action intent category that determines emoji candidates.
                   Defaults to "general" when None or unrecognized.

    Returns:
        An EmojiResult with the selected emoji.
    """
    if not text or not isinstance(text, str) or not text.strip():
        return EmojiResult(emoji=DEFAULT_EMOJI)
    action = WORKTYPE_TO_ACTION.get(work_type, "general")
    if action not in ACTION_EMOJI_MAP:
        action = "general"
    candidates = ACTION_EMOJI_MAP[action]
    return EmojiResult(emoji=random.choice(candidates))


def clear_cache() -> None:
    """Clear the in-memory embedding cache (for testing)."""
    global _embedding_cache
    _embedding_cache = None
