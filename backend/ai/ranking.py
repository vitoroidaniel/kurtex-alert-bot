"""Result cardinality for repair and unit ranking queries."""
import re

def ranking_limit(question, default=5):
    q = question.lower().strip()
    explicit = re.search(r"\b(?:top|first|show|list|give me)\s+(\d{1,3})\b|\b(\d{1,3})\s+(?:most expensive|highest|costliest|cheapest|lowest)\b", q)
    if explicit:
        return min(50, max(1, int(explicit.group(1) or explicit.group(2))))
    words = {"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10}
    named = re.search(r"\b(?:top|first)\s+(one|two|three|four|five|six|seven|eight|nine|ten)\b",q)
    if named:
        return words[named.group(1)]
    if re.search(r"\b(?:repairs|cases|trucks|units|trailers|reefers|shops|costs|amounts)\b",q):
        return default
    if re.search(r"\b(?:most expensive|highest|costliest|cheapest|lowest|most costly|maximum|minimum)\b",q):
        return 1
    return default
