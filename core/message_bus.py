"""Shared message bus for agent-to-agent communication."""
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Message:
    from_agent: str
    to_agent: str
    content: str
    response: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))


class MessageBus:
    def __init__(self):
        self._log: list[Message] = []
        self._lock = threading.Lock()

    def send(self, from_agent: str, to_agent: str, content: str) -> Message:
        msg = Message(from_agent=from_agent, to_agent=to_agent, content=content)
        with self._lock:
            self._log.append(msg)
        return msg

    def reply(self, msg: Message, response: str):
        msg.response = response  # atomic string assignment, no lock needed

    @property
    def log(self) -> list[Message]:
        return self._log

    def print_log(self):
        if not self._log:
            return
        print("\n" + "━" * 70)
        print("  💬 TOÀN BỘ AGENT-TO-AGENT CONVERSATIONS")
        print("━" * 70)
        for i, msg in enumerate(self._log, 1):
            print(f"\n  [{i}] [{msg.timestamp}] 📨 {msg.from_agent} → {msg.to_agent}:")
            for line in msg.content.strip().splitlines()[:6]:
                print(f"      {line}")
            if msg.response:
                print(f"\n      📩 {msg.to_agent} answers:")
                for line in msg.response.strip().splitlines()[:6]:
                    print(f"      {line}")
        print("\n" + "━" * 70)
