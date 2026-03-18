"""MySearch 通用 key ring。"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from mysearch.config import MySearchConfig, ProviderConfig


@dataclass(frozen=True, slots=True)
class KeyRecord:
    provider: str
    key: str
    source: str
    label: str


class MySearchKeyRing:
    def __init__(self, config: MySearchConfig) -> None:
        self.config = config
        self._lock = Lock()
        self._keys: dict[str, list[KeyRecord]] = {
            "tavily": [],
            "firecrawl": [],
            "exa": [],
            "xai": [],
        }
        self._indexes = {
            "tavily": 0,
            "firecrawl": 0,
            "exa": 0,
            "xai": 0,
        }
        self.reload()

    def reload(self) -> None:
        with self._lock:
            self._keys["tavily"] = self._load_provider(self.config.tavily)
            self._keys["firecrawl"] = self._load_provider(self.config.firecrawl)
            self._keys["exa"] = self._load_provider(self.config.exa)
            self._keys["xai"] = self._load_provider(self.config.xai)
            for provider, keys in self._keys.items():
                if self._indexes[provider] >= len(keys):
                    self._indexes[provider] = 0

    def get_next(self, provider: str) -> KeyRecord | None:
        with self._lock:
            keys = self._keys[provider]
            if not keys:
                return None
            index = self._indexes[provider]
            self._indexes[provider] = (index + 1) % len(keys)
            return keys[index]

    def has_provider(self, provider: str) -> bool:
        with self._lock:
            return bool(self._keys.get(provider))

    def describe(self) -> dict[str, dict[str, object]]:
        with self._lock:
            result: dict[str, dict[str, object]] = {}
            for provider, keys in self._keys.items():
                result[provider] = {
                    "count": len(keys),
                    "sources": sorted({key.source for key in keys}),
                    "labels": [key.label for key in keys],
                }
            return result

    def _load_provider(self, provider: ProviderConfig) -> list[KeyRecord]:
        loaded: list[KeyRecord] = []

        for index, key in enumerate(provider.api_keys, start=1):
            cleaned = key.strip()
            if not cleaned:
                continue
            loaded.append(
                KeyRecord(
                    provider=provider.name,
                    key=cleaned,
                    source="env",
                    label=f"{provider.name}:env:{index}",
                )
            )

        if provider.keys_file and provider.keys_file.exists():
            loaded.extend(self._load_from_file(provider))

        deduped: list[KeyRecord] = []
        seen: set[str] = set()
        for record in loaded:
            if record.key in seen:
                continue
            seen.add(record.key)
            deduped.append(record)
        return deduped

    def _load_from_file(self, provider: ProviderConfig) -> list[KeyRecord]:
        records: list[KeyRecord] = []
        assert provider.keys_file is not None

        for line_no, raw_line in enumerate(
            provider.keys_file.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = [item.strip() for item in line.split(",") if item.strip()]
            key = parts[-1] if parts else line
            label = parts[0] if len(parts) >= 2 else f"{provider.keys_file.name}:{line_no}"
            if not key:
                continue

            records.append(
                KeyRecord(
                    provider=provider.name,
                    key=key,
                    source="file",
                    label=label,
                )
            )

        return records
