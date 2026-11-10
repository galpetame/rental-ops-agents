"""fleet_core: the part of a bike-rental operation that an agent is not allowed to get wrong.

Agents read customer messages on Telegram, WhatsApp and Slack and PROPOSE actions.
This core decides. It owns availability, deposits, hand-overs, returns and maintenance,
applies every message at most once, and sends anything it cannot settle to a person.

Standard library only. Days are integers; a booking covers [start, end).
"""
from __future__ import annotations

from dataclasses import dataclass, field

SERVICE_KM = 3000          # a bike goes to service this many km after the last one
ACTIVE = ("held", "confirmed", "out")


@dataclass
class Bike:
    id: str
    model: str
    odo: int = 0
    last_service_odo: int = 0
    in_service: bool = False


@dataclass
class Booking:
    id: str
    bike_id: str
    customer: str
    start: int
    end: int
    status: str = "held"   # held -> confirmed -> out -> returned | cancelled
    deposit: bool = False
    license_ok: bool = False


@dataclass
class Fleet:
    bikes: dict
    bookings: dict = field(default_factory=dict)
    results: dict = field(default_factory=dict)      # message id -> result, for idempotency
    exceptions: list = field(default_factory=list)   # work for a person
    log: list = field(default_factory=list)

    # ------------------------------------------------------------ queries
    def free(self, bike_id, start, end, ignore=None):
        return not any(b.bike_id == bike_id and b.status in ACTIVE and b.id != ignore
                       and b.start < end and start < b.end for b in self.bookings.values())

    def available(self, start, end, model=None):
        return [b.id for b in self.bikes.values()
                if not b.in_service and (model is None or b.model == model) and self.free(b.id, start, end)]

    # ------------------------------------------------------------ entry point
    def handle(self, msg_id, channel, action, **args):
        """Apply one proposed action. The same msg_id always returns the first result."""
        if msg_id in self.results:
            return {**self.results[msg_id], "duplicate": True}
        fn = getattr(self, "_do_" + str(action), None)
        try:
            res = fn(**args) if fn else self._escalate("unknown_action", action=action, args=args)
        except TypeError as e:  # the agent proposed an action with the wrong arguments
            res = self._escalate("bad_arguments", action=action, error=str(e))
        res = {"msg_id": msg_id, "channel": channel, "action": action, **res}
        self.results[msg_id] = res
        self.log.append(res)
        return res

    def _escalate(self, reason, **packet):
        ticket = {"id": f"X{len(self.exceptions) + 1}", "reason": reason, "status": "open", **packet}
        self.exceptions.append(ticket)
        return {"ok": False, "escalated": reason, "ticket": ticket["id"], "say": "A person will get back to you shortly."}

    def _booking(self, booking_id, *allowed):
        b = self.bookings.get(booking_id)
        return b if b and b.status in allowed else None

    # ------------------------------------------------------------ actions
    def _do_quote(self, start, end, model=None):
        ids = self.available(start, end, model)
        return {"ok": True, "available": len(ids), "say": f"{len(ids)} bikes free for those dates."}

    def _do_hold(self, customer, start, end, model=None):
        if end <= start:
            return self._escalate("bad_dates", customer=customer, start=start, end=end)
        ids = self.available(start, end, model)
        if not ids:
            return {"ok": False, "say": "Nothing free for those dates.", "alternatives": len(self.available(start, end))}
        b = Booking(f"B{len(self.bookings) + 1}", ids[0], customer, start, end)
        self.bookings[b.id] = b
        return {"ok": True, "booking": b.id, "bike": b.bike_id, "say": f"Held {b.bike_id}. Pay the deposit to confirm."}

    def _do_confirm(self, booking, deposit_paid):
        b = self._booking(booking, "held")
        if not b:
            return self._escalate("confirm_without_hold", booking=booking)
        if not deposit_paid:
            return {"ok": False, "say": "The booking is confirmed once the deposit arrives."}
        b.deposit, b.status = True, "confirmed"
        return {"ok": True, "booking": b.id, "say": "Confirmed."}

    def _do_handover(self, booking, day, license_ok, odo):
        b = self._booking(booking, "confirmed")
        if not b:
            return self._escalate("handover_without_confirmed_booking", booking=booking)
        bike = self.bikes[b.bike_id]
        if not license_ok:
            return self._escalate("no_valid_license", booking=b.id, customer=b.customer)
        if any(o.bike_id == bike.id and o.status == "out" for o in self.bookings.values()):
            return self._escalate("previous_customer_not_back", booking=b.id, bike=bike.id, alternatives=self.available(b.start, b.end)[:3])
        if bike.in_service or not (b.start <= day < b.end) or odo < bike.odo:
            return self._escalate("handover_blocked", booking=b.id, bike=bike.id, in_service=bike.in_service, day=day, odo=odo)
        b.license_ok, b.status, bike.odo = True, "out", odo
        return {"ok": True, "booking": b.id, "say": f"{bike.id} handed over."}

    def _do_return(self, booking, day, odo, damage=False):
        b = self._booking(booking, "out")
        if not b:
            return self._escalate("return_without_handover", booking=booking)
        bike = self.bikes[b.bike_id]
        if odo < bike.odo:
            return self._escalate("odometer_went_back", booking=b.id, bike=bike.id, odo=odo)
        bike.odo, b.status = odo, "returned"
        late = max(0, day - b.end + 1) if day >= b.end else 0
        if damage:
            bike.in_service = True
            return self._escalate("damage_on_return", booking=b.id, bike=bike.id, late_days=late)
        if odo - bike.last_service_odo >= SERVICE_KM:
            bike.in_service = True
        return {"ok": True, "booking": b.id, "late_days": late, "to_service": bike.in_service, "say": "Returned. Deposit released."}

    def _do_extend(self, booking, new_end):
        b = self._booking(booking, "confirmed", "out")
        if not b or new_end <= b.end:
            return self._escalate("bad_extension", booking=booking, new_end=new_end)
        if not self.free(b.bike_id, b.end, new_end, ignore=b.id):
            return self._escalate("extension_conflict", booking=b.id, bike=b.bike_id, new_end=new_end)
        b.end = new_end
        return {"ok": True, "booking": b.id, "say": f"Extended to day {new_end}."}

    def _do_cancel(self, booking):
        b = self._booking(booking, "held", "confirmed")
        if not b:
            return self._escalate("cancel_not_possible", booking=booking)
        refund, b.status = b.deposit, "cancelled"
        return {"ok": True, "booking": b.id, "refund_deposit": refund, "say": "Cancelled."}

    # ------------------------------------------------------------ staff side
    def service_done(self, bike_id):
        bike = self.bikes[bike_id]
        bike.in_service, bike.last_service_odo = False, bike.odo

    def resolve(self, ticket_id):
        for t in self.exceptions:
            if t["id"] == ticket_id:
                t["status"] = "resolved"

    def expire_holds(self, day):
        """A hold with no deposit by its start day is released."""
        for b in self.bookings.values():
            if b.status == "held" and b.start <= day:
                b.status = "cancelled"

    def daily_report(self, day):
        bs = self.bookings.values()
        out = [b for b in bs if b.status == "out"]
        return {
            "day": day,
            "bikes": len(self.bikes),
            "out": len(out),
            "due_back_today": sorted(b.bike_id for b in out if b.end == day + 1),
            "overdue": sorted(b.bike_id for b in out if b.end <= day),
            "handovers_today": sorted(b.bike_id for b in bs if b.status == "confirmed" and b.start == day),
            "in_service": sorted(b.id for b in self.bikes.values() if b.in_service),
            "open_exceptions": [t["id"] + " " + t["reason"] for t in self.exceptions if t["status"] == "open"],
            "utilisation_pct": round(100 * len(out) / len(self.bikes)),
        }


def new_fleet(n=60):
    models = ["scooter-125"] * 3 + ["scooter-160"] * 2 + ["bike-300"]
    return Fleet({f"M{i:02d}": Bike(f"M{i:02d}", models[i % len(models)], odo=500 * (i % 7)) for i in range(1, n + 1)})
