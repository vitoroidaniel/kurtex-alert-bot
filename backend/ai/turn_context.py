"""Per-turn attachment routing, independent of Flask and provider clients."""
import re

_MEDIA_REFERENCE = re.compile(r"\b(?:image|photo|picture|video|attachment|attached|receipt|invoice|document|file|screenshot|frame|transcript|audio|poza|imaginea|factura|фото|видео|счет|чек)\b", re.I)
_FOLLOWUP = re.compile(r"^(?:what(?:'s| is) (?:this|that|it)|this|that|read it|describe it|explain it|summarize it|what does it say|how much is it|what is the total)[\s?.!]*$", re.I)

def is_social_turn(message):
    text = str(message or "").strip().lower()
    if re.fullmatch(r"(?:fuck off|fck off|fuck you|fck you|duten pula|du-te-n pula|du te in pula|иди нахуй|пошел нахуй)[\s!.]*", text):
        return "I’m here if you need help with something else."
    if re.fullmatch(r"(?:thanks|thank you|thx|mersi|mulțumesc|спасибо)[\s!.]*", text):
        return "You’re welcome."
    if re.fullmatch(r"(?:stop|never mind|nevermind|forget it|cancel)[\s!.]*", text):
        return "Understood. What would you like to work on next?"
    return None

def is_weather_turn(message):
    return bool(re.search(r"\b(?:weather|forecast|temperature outside|vremea|погода)\b", str(message or ""), re.I))

def select_attachment_ids(message, requested, history, available):
    if requested:
        return list(dict.fromkeys(str(x) for x in requested))
    if is_social_turn(message) or is_weather_turn(message):
        return []
    text = str(message or "").strip()
    if not (_MEDIA_REFERENCE.search(text) or _FOLLOWUP.fullmatch(text)):
        return []
    last_user = next((m for m in reversed(history) if m.get("role") == "user"), {})
    return [str(x) for x in last_user.get("attachment_ids", []) if str(x) in available]
