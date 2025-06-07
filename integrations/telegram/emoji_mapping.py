"""
Emoji mapping for Telegram reactions.

Maps invalid/unavailable emojis to valid Telegram reaction alternatives.
Based on API investigation showing only 74 reactions are available.
"""

# Valid Telegram reaction emojis (from API investigation)
# Only these 74 emojis can be used as reactions
VALID_TELEGRAM_REACTIONS = {
    "☃", "⚡", "✍", "❤", "❤‍🔥", "🆒", "🌚", "🌭", "🍌", "🍓", "🍾",
    "🎃", "🎄", "🎅", "🎉", "🏆", "🐳", "👀", "👌", "👍", "👎", "👏",
    "👨‍💻", "👻", "👾", "💅", "💊", "💋", "💔", "💘", "💩", "💯", "🔥",
    "🕊", "🖕", "🗿", "😁", "😂", "😇", "😈", "😍", "😎", "😐", "😘",
    "😡", "😢", "😨", "😭", "😱", "😴", "🙈", "🙉", "🙊", "🙏", "🤓",
    "🤔", "🤗", "🤝", "🤡", "🤣", "🤨", "🤩", "🤪", "🤬", "🤮", "🤯",
    "🤷", "🤷‍♀", "🤷‍♂", "🥰", "🥱", "🥴", "🦄", "🫡"
}

# Descriptions for each valid Telegram reaction emoji
# These help the LLM understand when to use each emoji
EMOJI_DESCRIPTIONS = {
    # Objects and symbols
    "☃": "snowman - winter, cold topics, holiday season",
    "⚡": "lightning - speed, energy, excitement, electricity, quick actions",
    "✍": "writing hand - writing, documentation, notes, signing",
    "🆒": "cool button - cool, awesome, stylish, modern",
    
    # Hearts and emotions
    "❤": "red heart - love, appreciation, health, vitality",
    "❤‍🔥": "heart on fire - passion, intense love, burning desire",
    "💔": "broken heart - sadness, heartbreak, disappointment",
    "💘": "heart with arrow - falling in love, cupid, romance",
    "💋": "kiss mark - kiss, romance, affection, beauty",
    "💅": "nail polish - beauty, style, sass, self-care",
    "💊": "pill - medicine, health, cure, solution",
    "💩": "poop - bad, terrible, joking, silly",
    "💯": "100 points - perfect, excellent, complete, achievement",
    
    # Nature and animals
    "🌚": "new moon face - mysterious, dark, secretive, night",
    "🐳": "whale - big, huge, massive, ocean",
    "🕊": "dove - peace, hope, freedom, spiritual",
    "🦄": "unicorn - magical, unique, rare, special, fantasy",
    
    # Food
    "🌭": "hot dog - food, lunch, casual eating",
    "🍌": "banana - fruit, silly, food, yellow",
    "🍓": "strawberry - sweet, fruit, delicious, red",
    "🍾": "champagne - celebration, success, party, achievement",
    
    # Holiday and celebration
    "🎃": "jack-o-lantern - Halloween, spooky, October, scary fun",
    "🎄": "Christmas tree - Christmas, holidays, December, festive",
    "🎅": "Santa - Christmas, gifts, holiday cheer, generosity",
    "🎉": "party popper - celebration, party, fun, success, achievement",
    "🏆": "trophy - winner, achievement, success, champion, first place",
    
    # Fire and energy
    "🔥": "fire - hot, trending, awesome, intense, burning, passionate",
    
    # Hand gestures
    "👀": "eyes - looking, watching, observing, noticing, attention",
    "👌": "OK hand - okay, perfect, good, approval",
    "👍": "thumbs up - good, yes, approval, success, positive",
    "👎": "thumbs down - bad, no, disapproval, negative, failure",
    "👏": "clapping - applause, congratulations, well done, bravo",
    "🖕": "middle finger - offensive, angry, rude (use carefully)",
    "🤝": "handshake - agreement, deal, cooperation, partnership",
    "🙏": "folded hands - please, thank you, prayer, hope, gratitude",
    
    # People and professions
    "👨‍💻": "technologist - coding, programming, tech, development, computer work",
    "👻": "ghost - spooky, Halloween, disappear, supernatural",
    "👾": "alien monster - gaming, retro, weird, strange",
    
    # Faces - positive emotions
    "😁": "beaming face - very happy, excited, cheerful, grinning",
    "😂": "joy - funny, hilarious, laughing hard, tears of joy",
    "😇": "halo - innocent, angelic, good, blessed, holy",
    "😍": "heart eyes - love, adore, amazing, beautiful, crush",
    "😎": "sunglasses - cool, confident, awesome, chill",
    "😘": "kiss - love, affection, flirting, cute",
    "🤗": "hugging - warm, welcoming, embrace, comfort, support",
    "🤣": "rolling on floor - extremely funny, hilarious, can't stop laughing",
    "🤩": "star eyes - amazed, impressed, excited, wonderful",
    "🥰": "hearts face - loved, warm feelings, affection, sweet",
    
    # Faces - negative emotions
    "😈": "smiling devil - mischievous, naughty, evil playful",
    "😐": "neutral - meh, indifferent, unimpressed, blank",
    "😡": "angry red - very angry, furious, mad, rage",
    "😢": "crying - sad, tears, upset, emotional",
    "😨": "fearful - scared, worried, nervous, anxious",
    "😭": "loudly crying - very sad, devastated, sobbing",
    "😱": "screaming - shocked, terrified, very scared, mind blown",
    "🤬": "cursing - very angry, swearing, furious, rage",
    "🤮": "vomiting - disgusting, sick, gross, terrible",
    
    # Faces - other emotions
    "😴": "sleeping - tired, bored, sleepy, zzz",
    "🤓": "nerd - smart, geeky, studious, knowledgeable",
    "🤔": "thinking - wondering, considering, puzzled, hmm",
    "🤡": "clown - silly, funny, foolish, circus, joke",
    "🤨": "raised eyebrow - skeptical, suspicious, doubtful, really?",
    "🤪": "crazy - wild, silly, zany, goofy, weird",
    "🤯": "exploding head - mind blown, shocked, amazed, wow",
    "🥱": "yawning - tired, bored, sleepy, exhausted",
    "🥴": "woozy - dizzy, confused, drunk, unwell",
    
    # Monkeys
    "🙈": "see no evil - embarrassed, shy, oops, don't want to see",
    "🙉": "hear no evil - not listening, ignoring, la la la",
    "🙊": "speak no evil - oops, secret, shouldn't have said that",
    
    # Shrugging
    "🤷": "shrug - don't know, whatever, confused, unsure",
    "🤷‍♀": "woman shrugging - don't know, whatever (female)",
    "🤷‍♂": "man shrugging - don't know, whatever (male)",
    
    # Other
    "🗿": "moai - stone face, serious, unmoved, stoic, deadpan",
    "🫡": "saluting - respect, yes sir, acknowledged, military"
}

# Mapping for invalid emojis to valid alternatives
# This ensures that commonly used emojis that aren't available as reactions
# are automatically converted to semantically similar valid reactions
EMOJI_MAPPING = {
    # Status emojis
    "✅": "👍",  # Checkmark -> Thumbs up
    "🚫": "👎",  # No entry -> Thumbs down
    "❌": "👎",  # X mark -> Thumbs down
    
    # Tool/action emojis
    "🔍": "👀",  # Magnifying glass -> Eyes
    "📊": "💯",  # Bar chart -> 100
    "🎨": "🎉",  # Art palette -> Party
    "🌐": "🌚",  # Globe -> Moon face
    "🔨": "🔥",  # Hammer -> Fire
    "✨": "⚡",  # Sparkles -> Lightning
    "🧠": "🤓",  # Brain -> Nerd face
    "💡": "⚡",  # Light bulb -> Lightning
    "🎯": "💯",  # Target -> 100
    "📈": "🏆",  # Chart up -> Trophy
    "🔧": "🔥",  # Wrench -> Fire
    "🚀": "⚡",  # Rocket -> Lightning
    "💫": "⚡",  # Dizzy -> Lightning
    "🌟": "🏆",  # Glowing star -> Trophy
    "⭐": "🏆",  # Star -> Trophy
    "📡": "🌚",  # Satellite antenna -> Moon face
    "⚙️": "🔥",  # Gear -> Fire
    "🔔": "👀",  # Bell -> Eyes
    "📢": "😱",  # Loudspeaker -> Scream
    "💬": "🤔",  # Speech bubble -> Thinking face
    "💭": "🤔",  # Thought bubble -> Thinking face
    "📝": "✍",  # Memo -> Writing hand
    "📋": "✍",  # Clipboard -> Writing hand
    "📌": "👀",  # Pushpin -> Eyes
    "📍": "👀",  # Round pushpin -> Eyes
    "🗂️": "🗿",  # Card index dividers -> Moai (for stability/organization)
    "📁": "🗿",  # File folder -> Moai
    "📂": "🗿",  # Open file folder -> Moai
    
    # Heart variants
    "❤️": "❤",  # Red heart with variant selector -> Plain red heart
    
    # Additional common mappings
    "🤖": "🤓",  # Robot -> Nerd face
    "🎮": "👾",  # Video game -> Alien monster
    "🍕": "🍌",  # Pizza -> Banana (both food)
    "🏃": "⚡",  # Running -> Lightning (speed)
    "📸": "👀",  # Camera -> Eyes
    "🎭": "🤡",  # Theater masks -> Clown
    "🛠️": "🔥",  # Tools -> Fire
    "⚠️": "😨",  # Warning -> Fearful face
    "🔒": "🗿",  # Lock -> Moai (security/solid)
    "🔓": "👍",  # Unlock -> Thumbs up
    "📣": "😱",  # Megaphone -> Scream
    "🏁": "🏆",  # Checkered flag -> Trophy
    "⏰": "⚡",  # Alarm clock -> Lightning (urgency)
    "🌍": "🌚",  # Earth globe -> Moon face
    "🌎": "🌚",  # Globe Americas -> Moon face
    "🌏": "🌚",  # Globe Asia -> Moon face
    "🔌": "⚡",  # Electric plug -> Lightning
    "🎪": "🤡",  # Circus tent -> Clown
    "🎢": "😱",  # Roller coaster -> Scream
    "🌈": "🦄",  # Rainbow -> Unicorn
    "☁️": "🌚",  # Cloud -> Moon face
    "⛈️": "😱",  # Thunder cloud -> Scream
    "🌙": "🌚",  # Crescent moon -> Moon face
    "🌞": "😁",  # Sun with face -> Beaming face
    "🔮": "🤓",  # Crystal ball -> Nerd face (mystical knowledge)
}


def get_valid_emoji(emoji: str) -> str:
    """
    Convert an emoji to a valid Telegram reaction.
    
    Args:
        emoji: The emoji to validate/convert
        
    Returns:
        A valid Telegram reaction emoji
    """
    # If it's already valid, return as-is
    if emoji in VALID_TELEGRAM_REACTIONS:
        return emoji
    
    # Try to map it to a valid alternative
    mapped = EMOJI_MAPPING.get(emoji)
    if mapped:
        return mapped
    
    # Default fallback
    return "🤔"  # Thinking face as universal fallback


def is_valid_reaction(emoji: str) -> bool:
    """
    Check if an emoji is a valid Telegram reaction.
    
    Args:
        emoji: The emoji to check
        
    Returns:
        True if the emoji can be used as a Telegram reaction
    """
    return emoji in VALID_TELEGRAM_REACTIONS