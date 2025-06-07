"""
Emoji mapping for Telegram reactions.

Maps invalid/unavailable emojis to valid Telegram reaction alternatives.
Based on API investigation showing only 74 reactions are available.
"""

# Valid Telegram reaction emojis (from API investigation)
# Only these 74 emojis can be used as reactions
# Note: Removed offensive emojis (💩, 🖕) from the list
VALID_TELEGRAM_REACTIONS = {
    "☃", "⚡", "✍", "❤", "❤‍🔥", "🆒", "🌚", "🌭", "🍌", "🍓", "🍾",
    "🎃", "🎄", "🎅", "🎉", "🏆", "🐳", "👀", "👌", "👍", "👎", "👏",
    "👨‍💻", "👻", "👾", "💅", "💊", "💋", "💔", "💘", "💯", "🔥",
    "🕊", "🗿", "😁", "😂", "😇", "😈", "😍", "😎", "😐", "😘",
    "😡", "😢", "😨", "😭", "😱", "😴", "🙈", "🙉", "🙊", "🙏", "🤓",
    "🤔", "🤗", "🤝", "🤡", "🤣", "🤨", "🤩", "🤪", "🤬", "🤮", "🤯",
    "🤷", "🤷‍♀", "🤷‍♂", "🥰", "🥱", "🥴", "🦄", "🫡"
}

# Descriptions for each valid Telegram reaction emoji
# These help the LLM understand when to use each emoji
EMOJI_DESCRIPTIONS = {
    # Objects and symbols
    "☃": "snowman - giving cold vibes, icy response, winter mood",
    "⚡": "lightning - fast af, electric energy, that was quick, zoom zoom",
    "✍": "writing hand - taking notes, receipts collected, documenting the tea",
    "🆒": "cool button - that's cool, nice, fresh, we chillin",
    
    # Hearts and emotions
    "❤": "red heart - love this, wholesome content, you're valid",
    "❤‍🔥": "heart on fire - this is fire, hot take, spicy opinion",
    "💔": "broken heart - pain, heartbreak, this ain't it, emotional damage",
    "💘": "heart with arrow - caught feelings, shipping this, cupid's work",
    "💋": "kiss mark - chef's kiss, mwah, perfect, slay",
    "💅": "nail polish - and that's on period, slay queen, serving looks",
    "💊": "pill - hard to swallow, tough pill, reality check, cope",
    "💯": "100 points - facts, no cap, absolutely, real talk, valid",
    
    # Nature and animals
    "🌚": "new moon face - side eye, sus, creeping, lurking energy",
    "🐳": "whale - big mood, whale of a time, massive flex",
    "🕊": "dove - peace out, rest in peace, sending good vibes",
    "🦄": "unicorn - rare find, one of a kind, extra special, main character energy",
    
    # Food
    "🌭": "hot dog - snack time, casual eats, random but ok",
    "🍌": "banana - going bananas, silly goose energy, potassium vibes",
    "🍓": "strawberry - sweet like that, berry cute, fresh",
    "🍾": "champagne - we poppin bottles, celebration mode, big wins only",
    
    # Holiday and celebration
    "🎃": "jack-o-lantern - spooky szn, Halloween vibes, boo",
    "🎄": "Christmas tree - festive af, holiday mood, cozy season",
    "🎅": "Santa - unrealistic expectations, too good to be true, cap",
    "🎉": "party popper - let's gooo, we did it, party time, W",
    "🏆": "trophy - you won, champion behavior, first place energy, goated",
    
    # Fire and energy
    "🔥": "fire - that's fire, lit, heat, bussin, absolutely slaps",
    
    # Hand gestures
    "👀": "eyes - I see you, watching this, eyes emoji, noticed that",
    "👌": "OK hand - perfect, chef's kiss, just right, mint",
    "👍": "thumbs up - bet, sounds good, approved, we gucci",
    "👎": "thumbs down - nah, not it, L, miss me with that",
    "👏": "clapping - period, facts, louder for those in back, tea",
    "🤝": "handshake - respect, we good, deal sealed, mutual understanding",
    "🙏": "folded hands - please bestie, blessed, grateful, manifesting",
    
    # People and professions
    "👨‍💻": "technologist - coding time, tech bro energy, debugging life",
    "👻": "ghost - ghosting, spooky vibes, disappeared, boo",
    "👾": "alien monster - gamer moment, retro vibes, weird flex but ok",
    
    # Faces - positive emotions
    "😁": "beaming face - big smile energy, living my best life, vibing",
    "😂": "joy - I'm dead, crying laughing, hilarious, deceased",
    "😇": "halo - innocent til proven guilty, being good, angel behavior",
    "😍": "heart eyes - obsessed, love this for us, stunning, need this",
    "😎": "sunglasses - cool kid alert, unbothered, too cool for this",
    "😘": "kiss - sending love, xoxo, cute, flirty vibes",
    "🤗": "hugging - hugs, wholesome content, supportive bestie, comfort",
    "🤣": "rolling on floor - LMAOOO, I can't, stop it, too funny",
    "🤩": "star eyes - shook, amazing, mind blown, obsessed",
    "🥰": "hearts face - soft hours, uwu energy, precious, wholesome",
    
    # Faces - negative emotions
    "😈": "smiling devil - menace behavior, chaos mode, up to no good",
    "😐": "neutral - bruh, deadass, no thoughts head empty, meh",
    "😡": "angry red - pressed, big mad, heated, rage mode activated",
    "😢": "crying - sad hours, in my feels, pain, crying in the club",
    "😨": "fearful - shook, scared, anxiety has entered the chat",
    "😭": "loudly crying - I'm crying, literally sobbing, can't handle this",
    "😱": "screaming - WHAT, I'm shook, plot twist, absolutely not",
    "🤬": "cursing - mad mad, choosing violence, absolutely livid",
    "🤮": "vomiting - ew, nasty, thanks I hate it, disgusting",
    
    # Faces - other emotions
    "😴": "sleeping - sleepy time, boring, snoozefest, catching z's",
    "🤓": "nerd - actually... , nerd alert, smart cookie, big brain time",
    "🤔": "thinking - hmm, thinking face, processing, let me think",
    "🤡": "clown - you're a clown, circus behavior, goofy, joke's on you",
    "🤨": "raised eyebrow - sus, side eye, doubt, the rock eyebrow",
    "🤪": "crazy - unhinged, chaotic energy, silly goose, quirky",
    "🤯": "exploding head - mind blown, WHAT, I can't even, shooketh",
    "🥱": "yawning - boring, sleepy, this ain't it, snooze",
    "🥴": "woozy - drunk thoughts, confused, lost the plot, wasted",
    
    # Monkeys
    "🙈": "see no evil - I didn't see that, embarrassing, cringe, hide",
    "🙉": "hear no evil - didn't hear nothing, selective hearing, ignoring",
    "🙊": "speak no evil - oops my bad, tea spilled, said too much",
    
    # Shrugging
    "🤷": "shrug - idk bestie, it is what it is, no clue, whatever",
    "🤷‍♀": "woman shrugging - girl idk, not my problem, whatever sis",
    "🤷‍♂": "man shrugging - bro idk, not sure, whatever dude",
    
    # Other
    "🗿": "moai - stone face, based, chad energy, unmoved, deadpan",
    "🫡": "saluting - yes chief, copy that, respect, at your service"
}



def is_valid_reaction(emoji: str) -> bool:
    """
    Check if an emoji is a valid Telegram reaction.
    
    Args:
        emoji: The emoji to check
        
    Returns:
        True if the emoji can be used as a Telegram reaction
    """
    return emoji in VALID_TELEGRAM_REACTIONS