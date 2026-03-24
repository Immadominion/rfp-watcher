from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class WatcherItem:
    """Normalised representation of a single discoverable item."""

    id: str
    title: str
    url: str = ""
    metadata: dict = field(default_factory=dict)

    def format_message(self) -> str:
        lines = [f"*{self.title}*"]
        if self.url:
            lines.append(self.url)
        for key, value in self.metadata.items():
            lines.append(f"• *{key}:* {value}")
        return "\n".join(lines)


class BaseWatcher(ABC):
    """All watchers must implement this interface."""

    #: Stable identifier stored in the DB — never change after first run.
    watcher_id: str
    #: Human-readable name shown in Telegram messages.
    label: str

    @abstractmethod
    def fetch_items(self) -> list[WatcherItem]:
        """Return all currently available items from the source."""
        ...
