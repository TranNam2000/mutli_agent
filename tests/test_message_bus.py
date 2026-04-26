"""Tests for core.message_bus — history + recent() for agent context injection."""
from core.message_bus import MessageBus, Message


def test_send_reply_stores_message():
    bus = MessageBus()
    msg = bus.send("A", "B", "hello")
    bus.reply(msg, "world")
    assert len(bus.log) == 1
    assert bus.log[0].response == "world"


def test_recent_only_completed_skips_pending():
    bus = MessageBus()
    bus.send("A", "B", "q1"); bus.reply(bus.log[0], "a1")
    bus.send("B", "A", "q2"); bus.reply(bus.log[1], "a2")
    bus.send("A", "B", "q3")  # pending — no reply

    done = bus.recent("B", n=5)
    assert len(done) == 2
    assert all(m.response for m in done)

    everything = bus.recent("B", n=5, only_completed=False)
    assert len(everything) == 3


def test_recent_limits_to_n():
    bus = MessageBus()
    for i in range(5):
        bus.send("A", "B", f"q{i}"); bus.reply(bus.log[-1], f"a{i}")
    r = bus.recent("B", n=2)
    assert len(r) == 2
    # Most-recent last
    assert r[-1].content == "q4"


def test_recent_filters_by_role():
    bus = MessageBus()
    bus.send("A", "B", "q1"); bus.reply(bus.log[0], "a1")
    bus.send("C", "D", "q2"); bus.reply(bus.log[1], "a2")  # unrelated
    r = bus.recent("B", n=5)
    assert len(r) == 1
    assert r[0].to_agent == "B"


def test_to_dict_list_serializable():
    import json
    bus = MessageBus()
    bus.send("A", "B", "hi"); bus.reply(bus.log[0], "yo")
    dump = json.dumps(bus.to_dict_list())   # must not raise
    assert '"from_agent": "A"' in dump
    assert '"response": "yo"' in dump
