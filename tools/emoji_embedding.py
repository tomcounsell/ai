"""Emoji embedding index for fast reaction selection.

Maps the 73 validated Telegram reaction emojis to descriptive feeling-word
embeddings via cosine similarity. Also supports Premium custom emoji via
a separate cached index of custom emoji sticker packs.

Standard emoji cache: data/emoji_embeddings.json
Custom emoji cache:   data/custom_emoji_embeddings.json

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
import os
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Cache location for pre-computed emoji embeddings
CACHE_PATH = Path(__file__).parent.parent / "data" / "emoji_embeddings.json"

# Cache location for custom emoji embeddings (Premium feature)
CUSTOM_CACHE_PATH = Path(__file__).parent.parent / "data" / "custom_emoji_embeddings.json"

# Default fallback emoji (thinking face)
DEFAULT_EMOJI = "\U0001f914"  # 🤔

# Placeholder character used for custom emoji in message text
CUSTOM_EMOJI_PLACEHOLDER = "\u2753"  # ❓ (replaced by entity rendering)

# Minimum delta by which custom emoji score must exceed standard to win
CUSTOM_EMOJI_DELTA = 0.05


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

# In-memory cache of custom emoji embeddings (loaded lazily)
_custom_embedding_cache: dict[str, list[float]] | None = None

# Flag indicating custom emoji indexing is disabled (non-Premium, API error)
_custom_emoji_disabled: bool = False


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


def find_best_emoji(feeling: str) -> EmojiResult:
    """Find the best reaction emoji for a given feeling word.

    Embeds the feeling text and finds the nearest emoji by cosine similarity,
    searching both standard and custom emoji embeddings.

    Custom emoji wins only when its similarity score exceeds the best standard
    match by at least ``CUSTOM_EMOJI_DELTA`` (0.05).

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

    from tools.knowledge_search import _compute_embedding, _cosine_similarity

    start = time.time()
    query_embedding = _compute_embedding(feeling.strip(), api_key)
    if not query_embedding:
        return default_result

    # Search standard emoji embeddings
    best_standard_emoji = DEFAULT_EMOJI
    best_standard_score = -1.0

    for emoji, emb in embeddings.items():
        score = _cosine_similarity(query_embedding, emb)
        if score > best_standard_score:
            best_standard_score = score
            best_standard_emoji = emoji

    # Search custom emoji embeddings (if available)
    best_custom_id: int | None = None
    best_custom_score = -1.0

    custom_embeddings = _load_custom_embeddings()
    if custom_embeddings:
        for key, emb in custom_embeddings.items():
            score = _cosine_similarity(query_embedding, emb)
            if score > best_custom_score:
                best_custom_score = score
                # Key format: "custom:{document_id}"
                try:
                    best_custom_id = int(key.split(":", 1)[1])
                except (ValueError, IndexError):
                    continue

    # Custom emoji wins only if it exceeds standard by CUSTOM_EMOJI_DELTA
    use_custom = (
        best_custom_id is not None and best_custom_score > best_standard_score + CUSTOM_EMOJI_DELTA
    )

    elapsed_ms = (time.time() - start) * 1000

    if use_custom:
        result = EmojiResult(
            emoji=best_standard_emoji,  # keep standard as fallback
            document_id=best_custom_id,
            is_custom=True,
            score=best_custom_score,
        )
        logger.debug(
            f"find_best_emoji({feeling!r}) -> custom:{best_custom_id} "
            f"(score={best_custom_score:.3f} vs std={best_standard_score:.3f}, "
            f"{elapsed_ms:.1f}ms)"
        )
    else:
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


def find_best_emoji_for_message(text: str) -> EmojiResult:
    """Find the best reaction emoji for a message.

    Extracts a short snippet from the message and finds the nearest emoji.

    Args:
        text: The message text to select a reaction for.

    Returns:
        An EmojiResult with the best matching emoji.
    """
    if not text or not isinstance(text, str) or not text.strip():
        return EmojiResult(emoji=DEFAULT_EMOJI)

    # Use first 100 chars as the sentiment/topic snippet
    snippet = text.strip()[:100]
    return find_best_emoji(snippet)


def _load_custom_embeddings() -> dict[str, list[float]]:
    """Load custom emoji embeddings from cache file.

    Returns cached custom emoji embeddings, or empty dict if unavailable.
    Custom emoji indexing is disabled when the account is non-Premium or
    the cache file doesn't exist (lazy build on first bridge start).
    """
    global _custom_embedding_cache

    if _custom_emoji_disabled:
        return {}

    if _custom_embedding_cache is not None:
        return _custom_embedding_cache

    if CUSTOM_CACHE_PATH.exists():
        try:
            data = json.loads(CUSTOM_CACHE_PATH.read_text())
            if isinstance(data, dict) and len(data) > 0:
                _custom_embedding_cache = data
                logger.info(f"Loaded custom emoji embeddings from cache ({len(data)} entries)")
                return _custom_embedding_cache
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Custom emoji cache corrupted: {e}")

    _custom_embedding_cache = {}
    return _custom_embedding_cache


async def build_custom_emoji_index(client) -> dict[str, list[float]]:
    """Query Telethon for custom emoji packs and build embedding index.

    Queries the Telegram API for all custom emoji sticker sets available
    to the account, extracts document IDs and descriptive labels, computes
    embeddings, and caches to ``CUSTOM_CACHE_PATH``.

    Args:
        client: An authenticated Telethon TelegramClient instance.

    Returns:
        Dict mapping ``"custom:{document_id}"`` to embedding vectors.
        Returns empty dict on failure (non-Premium, API error, etc.).
    """
    global _custom_embedding_cache, _custom_emoji_disabled

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning("OPENROUTER_API_KEY not set, skipping custom emoji index")
        return {}

    try:
        from telethon.tl.functions.messages import GetEmojiStickersRequest

        result = await client(GetEmojiStickersRequest(hash=0))
    except Exception as e:
        logger.warning(f"Custom emoji API call failed (non-Premium?): {e}")
        _custom_emoji_disabled = True
        return {}

    # Extract sticker sets from the result
    sticker_sets = getattr(result, "sets", [])
    if not sticker_sets:
        logger.info("No custom emoji sticker sets found")
        _custom_embedding_cache = {}
        return {}

    # Build document_id -> label mapping from all sticker sets
    labels: dict[int, str] = {}

    # The result contains documents with their sticker set associations
    documents = getattr(result, "documents", [])
    # Build set_id -> set_title lookup
    set_titles: dict[int, str] = {}
    for s in sticker_sets:
        set_titles[s.id] = getattr(s, "title", "")

    for doc in documents:
        doc_id = doc.id
        # Extract associated emoji character from attributes
        emoji_char = ""
        set_id = None
        for attr in getattr(doc, "attributes", []):
            if hasattr(attr, "alt"):
                emoji_char = attr.alt or ""
            if hasattr(attr, "stickerset"):
                stickerset_ref = attr.stickerset
                if hasattr(stickerset_ref, "id"):
                    set_id = stickerset_ref.id

        # Compose descriptive label from emoji + set title
        set_title = set_titles.get(set_id, "") if set_id else ""
        label_parts = []
        if emoji_char:
            label_parts.append(emoji_char)
        if set_title:
            label_parts.append(set_title)
        if label_parts:
            labels[doc_id] = " ".join(label_parts)

    if not labels:
        logger.info("No custom emoji labels extracted from sticker sets")
        _custom_embedding_cache = {}
        return {}

    # Compute embeddings
    from tools.knowledge_search import _compute_embedding

    logger.info(f"Computing custom emoji embeddings for {len(labels)} emoji...")
    embeddings: dict[str, list[float]] = {}

    for doc_id, label in labels.items():
        embedding = _compute_embedding(label, api_key)
        if embedding:
            embeddings[f"custom:{doc_id}"] = embedding
        else:
            logger.warning(f"Failed to compute embedding for custom emoji {doc_id}")

    # Save to cache
    if embeddings:
        try:
            CUSTOM_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CUSTOM_CACHE_PATH.write_text(json.dumps(embeddings))
            logger.info(f"Saved custom emoji embeddings to cache ({len(embeddings)} entries)")
        except OSError as e:
            logger.warning(f"Failed to save custom emoji cache: {e}")

    _custom_embedding_cache = embeddings
    return embeddings


async def rebuild_custom_emoji_index(client) -> dict[str, list[float]]:
    """Force-rebuild the custom emoji index, clearing any existing cache.

    Args:
        client: An authenticated Telethon TelegramClient instance.

    Returns:
        The rebuilt embedding dict.
    """
    global _custom_embedding_cache, _custom_emoji_disabled

    _custom_embedding_cache = None
    _custom_emoji_disabled = False

    # Remove existing cache file
    try:
        if CUSTOM_CACHE_PATH.exists():
            CUSTOM_CACHE_PATH.unlink()
    except OSError:
        pass

    return await build_custom_emoji_index(client)


def clear_cache() -> None:
    """Clear the in-memory embedding caches (for testing)."""
    global _embedding_cache, _custom_embedding_cache, _custom_emoji_disabled
    _embedding_cache = None
    _custom_embedding_cache = None
    _custom_emoji_disabled = False
