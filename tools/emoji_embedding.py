"""Emoji embedding index for fast reaction selection.

Maps the 73 validated Telegram reaction emojis to descriptive feeling-word
embeddings via cosine similarity. Used by the bridge to select contextual
reaction emojis in <50ms (after initial cache warm-up).

Cache: data/emoji_embeddings.json (computed once, loaded on subsequent starts).

Usage:
    from tools.emoji_embedding import find_best_emoji, find_best_emoji_for_message

    emoji = find_best_emoji("excited")        # -> "🔥" or similar
    emoji = find_best_emoji_for_message(text)  # -> contextual emoji
"""

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Cache location for pre-computed emoji embeddings
CACHE_PATH = Path(__file__).parent.parent / "data" / "emoji_embeddings.json"

# Default fallback emoji (thinking face)
DEFAULT_EMOJI = "\U0001f914"  # 🤔

# Descriptive labels for each of the 73 validated Telegram reaction emojis.
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
    "\U0001f595": "rude, angry, offensive, middle finger, frustrated",
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

    from tools.knowledge_search import _compute_embedding

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


def find_best_emoji(feeling: str) -> str:
    """Find the best reaction emoji for a given feeling word.

    Embeds the feeling text and finds the nearest emoji by cosine similarity.

    Args:
        feeling: A word or phrase describing the desired reaction
                 (e.g., "excited", "sad", "great work").

    Returns:
        The best matching emoji string, or the default thinking emoji on failure.
    """
    if not feeling or not isinstance(feeling, str) or not feeling.strip():
        return DEFAULT_EMOJI

    embeddings = _load_or_compute_embeddings()
    if not embeddings:
        return DEFAULT_EMOJI

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return DEFAULT_EMOJI

    from tools.knowledge_search import _compute_embedding, _cosine_similarity

    start = time.time()
    query_embedding = _compute_embedding(feeling.strip(), api_key)
    if not query_embedding:
        return DEFAULT_EMOJI

    best_emoji = DEFAULT_EMOJI
    best_score = -1.0

    for emoji, emb in embeddings.items():
        score = _cosine_similarity(query_embedding, emb)
        if score > best_score:
            best_score = score
            best_emoji = emoji

    elapsed_ms = (time.time() - start) * 1000
    logger.debug(
        f"find_best_emoji({feeling!r}) -> {best_emoji} (score={best_score:.3f}, {elapsed_ms:.1f}ms)"
    )

    return best_emoji


def find_best_emoji_for_message(text: str) -> str:
    """Find the best reaction emoji for a message.

    Extracts a short snippet from the message and finds the nearest emoji.

    Args:
        text: The message text to select a reaction for.

    Returns:
        The best matching emoji string, or the default thinking emoji on failure.
    """
    if not text or not isinstance(text, str) or not text.strip():
        return DEFAULT_EMOJI

    # Use first 100 chars as the sentiment/topic snippet
    snippet = text.strip()[:100]
    return find_best_emoji(snippet)


def clear_cache() -> None:
    """Clear the in-memory embedding cache (for testing)."""
    global _embedding_cache
    _embedding_cache = None
