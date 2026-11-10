"""What must be true of the fleet after every message, whatever customers and agents do."""
from fleet_core import ACTIVE

INVARIANTS = {
    "INV-1": "No double booking: two active bookings on one bike never overlap",
    "INV-2": "No bike leaves without a paid deposit and a checked license",
    "INV-3": "A bike in service is never out with a customer",
    "INV-4": "One effect per message: a redelivered message returns the first result and changes nothing",
    "INV-5": "Nothing is dropped: every message ends as a result in the log or as a ticket for a person",
    "INV-6": "Odometers only go up",
}


def assert_invariants(fleet, seen_odo=None):
    bs = [b for b in fleet.bookings.values() if b.status in ACTIVE]
    for i, a in enumerate(bs):
        for b in bs[i + 1:]:
            assert not (a.bike_id == b.bike_id and a.start < b.end and b.start < a.end), f"INV-1: {a.id} and {b.id} overlap on {a.bike_id}"
    for b in fleet.bookings.values():
        if b.status == "out":
            assert b.deposit and b.license_ok, f"INV-2: {b.id} is out without deposit or license"
            assert not fleet.bikes[b.bike_id].in_service, f"INV-3: {b.bike_id} is out while in service"
    assert len(fleet.log) == len(fleet.results), "INV-4: a message was applied more than once"
    tickets = {t["id"] for t in fleet.exceptions}
    for r in fleet.log:
        assert r.get("ok") is not None, f"INV-5: {r['msg_id']} has no outcome"
        if r.get("escalated"):
            assert r["ticket"] in tickets, f"INV-5: {r['msg_id']} was escalated to nowhere"
    if seen_odo is not None:
        for bike in fleet.bikes.values():
            assert bike.odo >= seen_odo.get(bike.id, 0), f"INV-6: {bike.id} odometer went back"
            seen_odo[bike.id] = bike.odo
