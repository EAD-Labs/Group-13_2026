"""Print the ordered interaction sequence of one request from the JSONL log.

    python trace.py --last          # most recent trace
    python trace.py <trace_id>      # a specific trace (a prefix is enough)
    python trace.py --list          # recent trace ids with their outcome
    python trace.py --decisions [trace_id]
                                    # only who handed what to whom, and the
                                    # trade-offs each agent made (last trace
                                    # if no id)
"""

import glob
import json
import os
import sys

import config

# Fields already shown in the fixed columns, or too noisy for one line.
_HIDDEN = {
    "ts", "level", "trace_id", "seq", "actor", "event", "duration_ms", "exception",
    # Large payloads, present only when config.LOG_FULL_PAYLOADS is on;
    # read them in the session file instead.
    "inputs", "prompt", "raw", "payload", "output", "passages", "lesson", "materials",
}


def load_events():
    base = os.path.join(config.LOG_DIR, config.LOG_FILE)
    # Rotated files are base.1 (newer) .. base.N (oldest); read oldest first.
    rotated = sorted(glob.glob(base + ".*"), key=lambda p: -int(p.rsplit(".", 1)[1]))
    events = []
    for path in rotated + ([base] if os.path.exists(base) else []):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return [e for e in events if e.get("trace_id")]


def by_trace(events):
    traces = {}
    for e in events:
        traces.setdefault(e["trace_id"], []).append(e)
    return traces


def print_trace(trace_id, events):
    # A trace can span several HTTP requests (the plan, then handouts/quiz and
    # citation clicks reuse its id) and seq restarts in each, so order by time.
    events = sorted(events, key=lambda e: (e.get("ts", ""), e.get("seq", 0)))
    print(f"trace {trace_id}  ({events[0]['ts']})")
    for e in events:
        ms = f"{e['duration_ms']}ms" if "duration_ms" in e else ""
        extra = " ".join(
            f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in e.items() if k not in _HIDDEN
        )
        print(f"  {e.get('seq', '?'):>3}  {e.get('actor', ''):<12} {e.get('event', ''):<22} {ms:>8}  {extra}")
    session_files = glob.glob(os.path.join(config.LOG_DIR, config.LOG_SESSION_DIR, f"*_{trace_id}.log"))
    if session_files:
        print(f"  full agent inputs/outputs: {session_files[0]}")


def print_decisions(trace_id, events):
    """The agent graph of one trace: hand-offs, then each agent's trade-offs."""
    events = sorted(events, key=lambda e: (e.get("ts", ""), e.get("seq", 0)))
    print(f"trace {trace_id}  ({events[0]['ts']})")
    for e in events:
        if e.get("event") == "agent.start":
            print(f"\n  {e['actor']}  (receives: {', '.join(e.get('upstream') or []) or 'request only'})")
        elif e.get("event") == "agent.decision":
            print(f"    - {e.get('decision', '?')}")
            print(f"        chose:  {e.get('chosen', '?')}")
            for key, label in (("rejected", "over:   "), ("given_up", "cost:   "), ("because", "because:")):
                if e.get(key):
                    print(f"        {label} {e[key]}")
        elif e.get("event") == "agent.end" and e.get("status") != "ok":
            print(f"    !! {e['actor']} failed")


def main(argv):
    events = load_events()
    if not events:
        print(f"No events found in {config.LOG_DIR}/{config.LOG_FILE}")
        return 1
    traces = by_trace(events)

    if argv and argv[0] == "--decisions":
        wanted = argv[1] if len(argv) > 1 else events[-1]["trace_id"]
        matches = [t for t in traces if t.startswith(wanted)]
        if not matches:
            print(f"No trace starting with {wanted}")
            return 1
        for trace_id in matches:
            print_decisions(trace_id, traces[trace_id])
        return 0

    if not argv or argv[0] == "--last":
        trace_id = events[-1]["trace_id"]
        print_trace(trace_id, traces[trace_id])
        return 0

    if argv[0] == "--list":
        for trace_id, evs in list(traces.items())[-20:]:
            final = next((e for e in reversed(evs) if e.get("event") == "human.response"), {})
            path = next((e.get("path") for e in evs if e.get("event") == "human.request"), "")
            print(f"{trace_id}  {evs[0]['ts']}  {path}  status={final.get('status', '?')}")
        return 0

    matches = [t for t in traces if t.startswith(argv[0])]
    if not matches:
        print(f"No trace starting with {argv[0]}")
        return 1
    for trace_id in matches:
        print_trace(trace_id, traces[trace_id])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
