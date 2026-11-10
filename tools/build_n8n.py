"""Generate the n8n workflows (importable JSON) and a diagram of each (SVG) from one description.

    python tools/build_n8n.py     writes n8n/*.json and docs/*.svg

The diagrams are drawn from the same node list as the JSON, so they cannot drift apart.
They are renderings of the workflow files, not screenshots of an n8n canvas.
"""
import json
import uuid
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
B = "n8n-nodes-base."
URL = "={{ $env.FLEET_URL }}"
KIND = {"trigger": "#b4481e", "ai": "#7a4fd1", "core": "#0b6b5c", "logic": "#5d646c", "out": "#2f6fb0"}

PROPOSER_PROMPT = (
    "You read one customer message for a bike rental shop and propose exactly one action. "
    "Allowed actions: quote, hold, confirm, handover, return, extend, cancel. "
    "Reply with JSON {action, args}. If the message is unclear, reply {action: null}. "
    "You never promise a bike, a price or a refund: the fleet core decides and writes the reply."
)

WORKFLOWS = [
    {
        "file": "01_inbound_message_router", "name": "Rental ops 1: inbound message router",
        "nodes": [
            ("Telegram in", B + "telegramTrigger", 1.1, {"updates": ["message"]}, "trigger"),
            ("WhatsApp in", B + "whatsAppTrigger", 1, {"updates": ["messages"]}, "trigger"),
            ("Slack in", B + "slackTrigger", 1, {"trigger": ["message"]}, "trigger"),
            ("Normalize", B + "set", 3.4, {"assignments": {"assignments": [
                {"name": "msg_id", "type": "string", "value": "={{ $json.message?.message_id ?? $json.messages?.[0]?.id ?? $json.ts }}"},
                {"name": "channel", "type": "string", "value": "={{ $json.message ? 'telegram' : ($json.messages ? 'whatsapp' : 'slack') }}"},
                {"name": "customer", "type": "string", "value": "={{ $json.message?.from?.id ?? $json.messages?.[0]?.from ?? $json.user }}"},
                {"name": "text", "type": "string", "value": "={{ $json.message?.text ?? $json.messages?.[0]?.text?.body ?? $json.text }}"}]}}, "logic"),
            ("Propose action (LLM)", "@n8n/n8n-nodes-langchain.agent", 1.7, {"promptType": "define", "text": "={{ $json.text }}", "hasOutputParser": True, "options": {"systemMessage": PROPOSER_PROMPT}}, "ai"),
            ("Fleet core decides", B + "httpRequest", 4.2, {"method": "POST", "url": URL + "/action", "sendBody": True, "specifyBody": "json",
                "jsonBody": "={{ { msg_id: $('Normalize').item.json.msg_id, channel: $('Normalize').item.json.channel, action: $json.output.action, args: $json.output.args } }}"}, "core"),
            ("Escalated?", B + "if", 2.2, {"conditions": {"conditions": [{"leftValue": "={{ $json.escalated }}", "rightValue": "", "operator": {"type": "string", "operation": "notEmpty", "singleValue": True}}], "combinator": "and"}}, "logic"),
            ("Ticket to #ops-exceptions", B + "slack", 2.2, {"select": "channel", "channelId": {"__rl": True, "mode": "name", "value": "#ops-exceptions"}, "text": "={{ $json.ticket }} {{ $json.escalated }} ({{ $json.channel }})"}, "out"),
            ("Which channel?", B + "switch", 3.2, {"rules": {"values": [
                {"outputKey": c, "conditions": {"conditions": [{"leftValue": "={{ $json.channel }}", "rightValue": c, "operator": {"type": "string", "operation": "equals"}}], "combinator": "and"}} for c in ("telegram", "whatsapp", "slack")]}}, "logic"),
            ("Reply on Telegram", B + "telegram", 1.2, {"chatId": "={{ $('Normalize').item.json.customer }}", "text": "={{ $json.say }}"}, "out"),
            ("Reply on WhatsApp", B + "whatsApp", 1, {"operation": "send", "recipientPhoneNumber": "={{ $('Normalize').item.json.customer }}", "textBody": "={{ $json.say }}"}, "out"),
            ("Reply on Slack", B + "slack", 2.2, {"select": "user", "user": {"__rl": True, "mode": "id", "value": "={{ $('Normalize').item.json.customer }}"}, "text": "={{ $json.say }}"}, "out"),
        ],
        "edges": [("Telegram in", 0, "Normalize"), ("WhatsApp in", 0, "Normalize"), ("Slack in", 0, "Normalize"),
                  ("Normalize", 0, "Propose action (LLM)"), ("Propose action (LLM)", 0, "Fleet core decides"), ("Fleet core decides", 0, "Escalated?"),
                  ("Escalated?", 0, "Ticket to #ops-exceptions"), ("Escalated?", 0, "Which channel?"), ("Escalated?", 1, "Which channel?"),
                  ("Which channel?", 0, "Reply on Telegram"), ("Which channel?", 1, "Reply on WhatsApp"), ("Which channel?", 2, "Reply on Slack")],
    },
    {
        "file": "02_daily_ops_report", "name": "Rental ops 2: daily report",
        "nodes": [
            ("Every day 07:00", B + "scheduleTrigger", 1.2, {"rule": {"interval": [{"field": "cronExpression", "expression": "0 7 * * *"}]}}, "trigger"),
            ("Day number", B + "set", 3.4, {"assignments": {"assignments": [{"name": "day", "type": "number", "value": "={{ Math.floor(($now.toMillis() - new Date($env.SEASON_START).getTime()) / 86400000) }}"}]}}, "logic"),
            ("Fleet core: report", B + "httpRequest", 4.2, {"url": URL + "/report", "sendQuery": True, "queryParameters": {"parameters": [{"name": "day", "value": "={{ $json.day }}"}]}}, "core"),
            ("Format digest", B + "code", 2, {"jsCode": "const r = $input.first().json;\nreturn [{ json: { ...r, text: `Day ${r.day}: ${r.out}/${r.bikes} out (${r.utilisation_pct}%). Hand-overs today: ${r.handovers_today.join(', ') || 'none'}. Due back: ${r.due_back_today.join(', ') || 'none'}. Overdue: ${r.overdue.join(', ') || 'none'}. In service: ${r.in_service.join(', ') || 'none'}. Open tickets: ${r.open_exceptions.length}.` } }];"}, "logic"),
            ("Post to #ops-daily", B + "slack", 2.2, {"select": "channel", "channelId": {"__rl": True, "mode": "name", "value": "#ops-daily"}, "text": "={{ $json.text }}"}, "out"),
            ("Anything overdue?", B + "if", 2.2, {"conditions": {"conditions": [{"leftValue": "={{ $json.overdue.length }}", "rightValue": 0, "operator": {"type": "number", "operation": "gt"}}], "combinator": "and"}}, "logic"),
            ("Telegram the owner", B + "telegram", 1.2, {"chatId": "={{ $env.OWNER_CHAT_ID }}", "text": "=Overdue bikes: {{ $json.overdue.join(', ') }}"}, "out"),
        ],
        "edges": [("Every day 07:00", 0, "Day number"), ("Day number", 0, "Fleet core: report"), ("Fleet core: report", 0, "Format digest"),
                  ("Format digest", 0, "Post to #ops-daily"), ("Format digest", 0, "Anything overdue?"), ("Anything overdue?", 0, "Telegram the owner")],
    },
    {
        "file": "03_tickets_to_people", "name": "Rental ops 3: tickets to people",
        "nodes": [
            ("Every 10 minutes", B + "scheduleTrigger", 1.2, {"rule": {"interval": [{"field": "minutes", "minutesInterval": 10}]}}, "trigger"),
            ("Fleet core: open tickets", B + "httpRequest", 4.2, {"url": URL + "/tickets"}, "core"),
            ("Only new ones", B + "removeDuplicates", 2, {"operation": "removeItemsSeenInPreviousExecutions", "dedupeValue": "={{ $json.id }}"}, "logic"),
            ("Who owns it?", B + "switch", 3.2, {"rules": {"values": [
                {"outputKey": "mechanic", "conditions": {"conditions": [{"leftValue": "={{ $json.reason }}", "rightValue": "damage_on_return", "operator": {"type": "string", "operation": "equals"}}], "combinator": "and"}},
                {"outputKey": "front desk", "conditions": {"conditions": [{"leftValue": "={{ ['no_valid_license','previous_customer_not_back','handover_blocked','extension_conflict'].includes($json.reason) }}", "rightValue": "", "operator": {"type": "boolean", "operation": "true", "singleValue": True}}], "combinator": "and"}}]},
                "options": {"fallbackOutput": "extra"}}, "logic"),
            ("Post to #mechanic", B + "slack", 2.2, {"select": "channel", "channelId": {"__rl": True, "mode": "name", "value": "#mechanic"}, "text": "={{ $json.id }} {{ $json.reason }}: bike {{ $json.bike }}"}, "out"),
            ("Post to #front-desk", B + "slack", 2.2, {"select": "channel", "channelId": {"__rl": True, "mode": "name", "value": "#front-desk"}, "text": "={{ $json.id }} {{ $json.reason }}: booking {{ $json.booking }}"}, "out"),
            ("Post to #ops-exceptions", B + "slack", 2.2, {"select": "channel", "channelId": {"__rl": True, "mode": "name", "value": "#ops-exceptions"}, "text": "={{ $json.id }} {{ $json.reason }}: {{ $json.text }}"}, "out"),
        ],
        "edges": [("Every 10 minutes", 0, "Fleet core: open tickets"), ("Fleet core: open tickets", 0, "Only new ones"), ("Only new ones", 0, "Who owns it?"),
                  ("Who owns it?", 0, "Post to #mechanic"), ("Who owns it?", 1, "Post to #front-desk"), ("Who owns it?", 2, "Post to #ops-exceptions")],
    },
]


def layout(wf):
    """Column = longest path from a trigger; rows stack inside a column."""
    col = {n[0]: 0 for n in wf["nodes"]}
    for _ in wf["nodes"]:
        for s, _, d in wf["edges"]:
            col[d] = max(col[d], col[s] + 1)
    rows, pos = {}, {}
    for name, *_ in wf["nodes"]:
        r = rows.get(col[name], 0)
        rows[col[name]] = r + 1
        pos[name] = (col[name], r)
    height = max(rows.values())
    return {n: (c, r + (height - rows[c]) / 2) for n, (c, r) in pos.items()}, max(col.values()) + 1, height


def to_json(wf):
    pos, _, _ = layout(wf)
    nodes = [{"parameters": p, "id": str(uuid.uuid5(uuid.NAMESPACE_URL, wf["file"] + n)), "name": n, "type": t, "typeVersion": v,
              "position": [int(240 * pos[n][0]), int(180 * pos[n][1])]} for n, t, v, p, _ in wf["nodes"]]
    conn = {}
    for s, out, d in wf["edges"]:
        outs = conn.setdefault(s, {"main": []})["main"]
        while len(outs) <= out:
            outs.append([])
        outs[out].append({"node": d, "type": "main", "index": 0})
    return {"name": wf["name"], "nodes": nodes, "connections": conn, "active": False, "settings": {"executionOrder": "v1"}, "pinData": {}}


def to_svg(wf):
    pos, cols, rows = layout(wf)
    W, H, GX, GY = 150, 54, 44, 26
    width, height = cols * (W + GX) + 16, rows * (H + GY) + 46
    xy = {n: (16 + c * (W + GX), 40 + r * (H + GY)) for n, (c, r) in pos.items()}
    kind = {n[0]: n[4] for n in wf["nodes"]}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" font-family="ui-sans-serif,system-ui,Segoe UI,Arial" font-size="12">',
           f'<rect width="{width}" height="{height}" fill="#14181b"/>',
           f'<text x="16" y="24" fill="#e9ecee" font-size="14" font-weight="700">{escape(wf["name"])}</text>']
    for s, _, d in wf["edges"]:
        (x1, y1), (x2, y2) = xy[s], xy[d]
        x1, y1, y2 = x1 + W, y1 + H / 2, y2 + H / 2
        out.append(f'<path d="M{x1} {y1} C{x1 + GX / 2} {y1},{x2 - GX / 2} {y2},{x2} {y2}" fill="none" stroke="#6b7680" stroke-width="1.4"/>')
    for n, (x, y) in xy.items():
        words, line1, line2 = n.split(" "), "", ""
        for w in words:
            if len(line1) + len(w) < 20 and not line2:
                line1 = (line1 + " " + w).strip()
            else:
                line2 = (line2 + " " + w).strip()
        out.append(f'<rect x="{x}" y="{y}" width="{W}" height="{H}" rx="9" fill="#1e2428" stroke="{KIND[kind[n]]}" stroke-width="1.6"/>'
                   f'<rect x="{x}" y="{y}" width="6" height="{H}" rx="3" fill="{KIND[kind[n]]}"/>')
        ys = [y + 32] if not line2 else [y + 24, y + 40]
        for t, ty in zip([line1, line2], ys):
            out.append(f'<text x="{x + 16}" y="{ty}" fill="#e9ecee">{escape(t)}</text>')
    return "\n".join(out) + "\n</svg>\n"


if __name__ == "__main__":
    (ROOT / "n8n").mkdir(exist_ok=True)
    (ROOT / "docs").mkdir(exist_ok=True)
    for wf in WORKFLOWS:
        (ROOT / "n8n" / f"{wf['file']}.json").write_text(json.dumps(to_json(wf), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (ROOT / "docs" / f"{wf['file']}.svg").write_text(to_svg(wf), encoding="utf-8")
        print(wf["file"], len(wf["nodes"]), "nodes")
