"""agent: turns a customer message into a PROPOSED action. It never touches the fleet.

The offline proposer below is rule-based so the whole project runs without a model or a key.
In production this function is the LLM call; its output has the same shape and goes through
the same core, which is why swapping the model cannot create a double booking.
"""
import re

DAYS = re.compile(r"\bday\s*(\d+)\s*(?:-|to|until)\s*(?:day\s*)?(\d+)", re.I)
BOOKING = re.compile(r"\b(B\d+)\b")
MODEL = re.compile(r"\b(scooter-125|scooter-160|bike-300)\b", re.I)
ODO = re.compile(r"\b(?:odo|odometer)\D{0,4}(\d{2,6})", re.I)
DAY = re.compile(r"\bday\s*(\d+)", re.I)


def propose(text, customer):
    """Return (action, args) or None when the message is not clear enough to act on."""
    t = text.lower()
    b, m, d = BOOKING.search(text), MODEL.search(text), DAYS.search(text)
    model = m.group(1).lower() if m else None
    if b and "paid" in t and "deposit" in t:
        return "confirm", {"booking": b.group(1), "deposit_paid": True}
    if b and ("pick" in t or "handover" in t or "collect" in t):
        o, day = ODO.search(text), DAY.search(text)
        if o and day:
            return "handover", {"booking": b.group(1), "day": int(day.group(1)), "odo": int(o.group(1)),
                                "license_ok": "no license" not in t and "license" in t}
        return None
    if b and "return" in t:
        o, day = ODO.search(text), DAY.search(text)
        if o and day:
            return "return", {"booking": b.group(1), "day": int(day.group(1)), "odo": int(o.group(1)),
                              "damage": "damage" in t or "scratch" in t or "crash" in t}
        return None
    if b and "extend" in t:
        day = DAY.search(text)
        return ("extend", {"booking": b.group(1), "new_end": int(day.group(1))}) if day else None
    if b and "cancel" in t:
        return "cancel", {"booking": b.group(1)}
    if d and ("book" in t or "rent" in t or "reserve" in t):
        return "hold", {"customer": customer, "start": int(d.group(1)), "end": int(d.group(2)), "model": model}
    if d and ("available" in t or "free" in t or "price" in t):
        return "quote", {"start": int(d.group(1)), "end": int(d.group(2)), "model": model}
    return None


def run(fleet, msg_id, channel, customer, text):
    """One inbound message, end to end: propose, let the core decide, return the reply."""
    if msg_id in fleet.results:
        return {**fleet.results[msg_id], "duplicate": True}
    p = propose(text, customer)
    if p is None:
        res = fleet._escalate("unclear_message", customer=customer, text=text[:200])
        res = {"msg_id": msg_id, "channel": channel, "action": None, **res}
        fleet.results[msg_id] = res
        fleet.log.append(res)
        return res
    action, args = p
    return fleet.handle(msg_id, channel, action, **args)
