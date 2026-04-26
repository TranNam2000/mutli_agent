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

    def to_dict(self, index: int | None = None) -> dict:
        """Serialize for JSON export."""
        d = {
            "timestamp":  self.timestamp,
            "from_agent": self.from_agent,
            "to_agent":   self.to_agent,
            "content":    self.content,
            "response":   self.response,
        }
        if index is not None:
            d = {"index": index, **d}
        return d


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

    def recent(self, agent_role: str, n: int = 3,
               *, only_completed: bool = True) -> list[Message]:
        """
        Return up to `n` most recent messages where `agent_role` is involved
        (either asker or responder).

        Used by `BaseAgent.respond_to` so an agent doesn't answer a question
        with zero memory of what it just told its colleague 2 turns ago.

        Parameters
        ----------
        only_completed
            Skip messages still awaiting a reply — i.e. the current question
            the caller is about to answer. Default True avoids feeding the
            agent its own pending prompt.
        """
        with self._lock:
            involved = [
                m for m in self._log
                if m.from_agent == agent_role or m.to_agent == agent_role
            ]
        if only_completed:
            involved = [m for m in involved if m.response]
        return involved[-max(1, n):] if involved else []

    def to_dict_list(self) -> list[dict]:
        """Serialize full log for JSON export."""
        return [m.to_dict(index=i) for i, m in enumerate(self._log, 1)]

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
