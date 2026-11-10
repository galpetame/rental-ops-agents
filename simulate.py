"""Simulate a season on a synthetic 60-bike fleet and report what the agents handled alone.

    python simulate.py            # 14 days, seed 7; writes out/season.json and out/season.md
    python simulate.py 30 3       # days, seed

Customers are generated. Nothing here is a real client's data.
"""
import json
import random
import sys
from pathlib import Path

from agent import run
from fleet_core import new_fleet
from invariants import assert_invariants

CHANNELS = ["whatsapp"] * 5 + ["telegram"] * 3 + ["slack"]
MODELS = ["scooter-125", "scooter-160", "bike-300", None]
UNCLEAR = ["hi is the shop open", "how much", "can my friend come too?", "ok thanks", "where are you located"]


def season(days=14, seed=7, bikes=60, check=True):
    rng, fleet, odo, n = random.Random(seed), new_fleet(bikes), {}, 0
    trips, reports = {}, []   # booking id -> state the simulated customer remembers

    def send(customer, text, channel=None):
        nonlocal n
        n += 1
        mid, channel = f"m{n}", channel or rng.choice(CHANNELS)
        res = run(fleet, mid, channel, customer, text)
        if rng.random() < 0.10:                      # the channel redelivers the same message
            again = run(fleet, mid, channel, customer, text)
            assert again.get("duplicate") and again["ok"] == res["ok"]
        if check:
            assert_invariants(fleet, odo)
        return res

    for day in range(days):
        fleet.expire_holds(day)
        for bike in [b for b in fleet.bikes.values() if b.in_service]:   # the mechanic clears about half a day
            if rng.random() < 0.5:
                fleet.service_done(bike.id)
        for t in [t for t in fleet.exceptions if t["status"] == "open"]:  # staff work the queue
            if rng.random() < 0.7:
                fleet.resolve(t["id"])
        for _ in range(rng.randint(10, 22)):                              # new enquiries
            c = f"cust{rng.randint(1000, 9999)}"
            if rng.random() < 0.08:
                send(c, rng.choice(UNCLEAR))
                continue
            start = day + rng.randint(0, 3)
            end, model = start + rng.randint(1, 6), rng.choice(MODELS)
            if rng.random() < 0.3:
                send(c, f"is anything available day {start} to {end}?")
            r = send(c, f"I want to book a {model or 'bike'} from day {start} to {end}")
            if r.get("booking"):
                trips[r["booking"]] = {"c": c, "start": start, "end": end, "state": "held"}
                if rng.random() < 0.85:
                    if send(c, f"paid the deposit for {r['booking']}").get("ok"):
                        trips[r["booking"]]["state"] = "confirmed"
        for bid, t in list(trips.items()):
            b = fleet.bookings[bid]
            if t["state"] == "confirmed" and b.start == day and b.status == "confirmed":
                if rng.random() < 0.04:
                    send(t["c"], f"cancel {bid} please")
                    t["state"] = "done"
                    continue
                lic = "license shown" if rng.random() > 0.05 else "no license with me"
                odo_now = fleet.bikes[b.bike_id].odo
                r = send(t["c"], f"here to pick up {bid}, day {day}, odo {odo_now}, {lic}")
                t["state"] = "out" if r.get("ok") else "done"
            elif t["state"] == "out" and b.status == "out":
                if day == b.end - 1 and rng.random() < 0.12:
                    send(t["c"], f"can I extend {bid} to day {b.end + rng.randint(1, 3)}")
                back = b.end - 1 + (1 if rng.random() < 0.08 else 0)
                if day >= back:
                    km = rng.randint(40, 320) * max(1, day - b.start + 1)
                    dmg = ", small scratch on the side" if rng.random() < 0.04 else ""
                    send(t["c"], f"return {bid} day {day} odo {fleet.bikes[b.bike_id].odo + km}{dmg}")
                    t["state"] = "done"
        reports.append(fleet.daily_report(day))

    log = fleet.log
    esc = [r for r in log if r.get("escalated")]
    by_reason = {}
    for r in esc:
        by_reason[r["escalated"]] = by_reason.get(r["escalated"], 0) + 1
    return fleet, reports, {
        "simulated": True, "days": days, "seed": seed, "bikes": bikes,
        "messages": len(log),
        "handled_without_a_person": len(log) - len(esc),
        "handled_without_a_person_pct": round(100 * (len(log) - len(esc)) / len(log), 1),
        "sent_to_a_person": len(esc),
        "sent_to_a_person_by_reason": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])),
        "bookings_created": len(fleet.bookings),
        "handovers": sum(1 for r in log if r["action"] == "handover" and r["ok"]),
        "returns": sum(1 for r in log if r["action"] == "return" and r["ok"]),
        "by_channel": {c: sum(1 for r in log if r["channel"] == c) for c in ("whatsapp", "telegram", "slack")},
        "peak_utilisation_pct": max(r["utilisation_pct"] for r in reports),
        "invariant_checks": len(log) if check else 0,
    }


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    fleet, reports, m = season(days, seed)
    out = Path(__file__).resolve().parent / "out"
    out.mkdir(exist_ok=True)
    (out / "season.json").write_text(json.dumps({"metrics": m, "daily": reports}, indent=1), encoding="utf-8")
    lines = [f"# Simulated season: {m['bikes']} bikes, {days} days, seed {seed}", "",
             "Synthetic customers. Not a real client's data.", "",
             f"- Messages: {m['messages']} ({', '.join(f'{k} {v}' for k, v in m['by_channel'].items())})",
             f"- Handled without a person: {m['handled_without_a_person']} ({m['handled_without_a_person_pct']}%)",
             f"- Sent to a person: {m['sent_to_a_person']}",
             *[f"  - {k}: {v}" for k, v in m["sent_to_a_person_by_reason"].items()],
             f"- Bookings {m['bookings_created']}, hand-overs {m['handovers']}, returns {m['returns']}",
             f"- Peak utilisation: {m['peak_utilisation_pct']}%",
             f"- Invariants asserted after each of {m['invariant_checks']} messages: no violation", "",
             "| Day | Out | Due back | Overdue | In service | Open tickets | Utilisation |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {r['day']} | {r['out']} | {len(r['due_back_today'])} | {len(r['overdue'])} | {len(r['in_service'])} | {len(r['open_exceptions'])} | {r['utilisation_pct']}% |" for r in reports]
    (out / "season.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:14]))
