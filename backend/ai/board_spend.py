"""Deterministic, composable board spending analytics over verified records."""
import re
from collections import defaultdict
from decimal import Decimal

def spending_answer(question, records):
    q = question.lower()
    thresholds = list(re.finditer(r"\b(under|below|less\s+than|over|above|more\s+than)\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", q))
    under = [Decimal(m[2].replace(",", "")) for m in thresholds if m[1] in ("under","below","less than")]
    over = [Decimal(m[2].replace(",", "")) for m in thresholds if m[1] in ("over","above","more than")]
    shops = bool(re.search(r"\b(?:top|highest|leading|most expensive)\s+(?:\d+\s+)?shops\b|shops?\s+by\s+(?:spend|cost)", q))
    compare = bool(under and over)
    overall = bool(re.search(r"\b(?:spend breakdown|cost breakdown|total spend|repair spend|how much (?:was |have we )?spent)\b", q))
    if not (compare or shops or overall):
        return None
    # Requests mixing unrelated analytical dimensions must go to the planner, not be partially answered.
    if re.search(r"\b(?:unassigned|high.priority|handover|breakdowns?|driver|trailer|truck|agent|yesterday|last week|last month|historical|closed)\b",q):
        return None
    priced = [(Decimal(str(r["cost"])),r) for r in records if r.get("cost") is not None]
    total = sum((amt for amt,_ in priced),Decimal(0))
    money=lambda x: f"${x:,.2f}"
    lines=["### Repair spend",f"Current Live Board · {len(priced)} of {len(records)} cases have recorded amounts.",f"**Total recorded: {money(total)}**",""]
    if compare or overall or thresholds:
        lines.append("### Cost breakdown")
        if not thresholds:
            under,over=[Decimal(500)],[Decimal(500)]
        for value in dict.fromkeys(under):
            bucket=[a for a,_ in priced if a<value]
            lines.append(f"- Under {money(value)}: **{len(bucket)} cases · {money(sum(bucket,Decimal(0)))}**")
        for value in dict.fromkeys(over):
            bucket=[a for a,_ in priced if a>=value]
            lines.append(f"- {money(value)} and above: **{len(bucket)} cases · {money(sum(bucket,Decimal(0)))}**")
        if under and over and set(under)==set(over):
            lines.append("Amounts equal to the threshold are included in the upper tier.")
    if shops:
        grouped=defaultdict(lambda:[0,Decimal(0)])
        unknown=0
        for amt,r in priced:
            name=str(r.get("shop") or "").strip()
            if not name:
                unknown+=1
                continue
            item=grouped[name.casefold()]
            item[0]+=1
            item[1]+=amt
            if len(item)==2:item.append(name)
        n=re.search(r"\btop\s+(\d{1,2})\s+shops\b",q)
        limit=min(50,max(1,int(n[1]))) if n else 5
        lines.extend(["","### Top shops by recorded spend"])
        ranked=sorted(grouped.values(),key=lambda x:(-x[1],x[2].casefold()))
        for count,amt,name in ranked[:limit]:
            lines.append(f"- **{name}** — {money(amt)} · {count} case(s)")
        if not ranked:lines.append("No shop names are recorded on priced cases.")
        if unknown:lines.append(f"{unknown} priced case(s) have no recorded shop.")
    return "\n".join(lines)
