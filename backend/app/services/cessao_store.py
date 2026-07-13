from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from flask import Request
from werkzeug.datastructures import MultiDict

from ..core.config import Settings
from ..domain.models import CessaoRecord
from .json_loader import iter_json_array
from .normalizer import fold_text, infer_source_metadata, normalize_publication


@dataclass(frozen=True, slots=True)
class QueryFilters:
    estado: str | None = None
    tribunal: str | None = None
    confianca: str | None = None
    classe: str | None = None
    search: str | None = None
    ano: int | None = None
    descoberto_em: str | None = None
    offset: int = 0
    limit: int = 100

    @classmethod
    def from_request_args(
        cls,
        args: MultiDict[str, str],
        settings: Settings,
    ) -> "QueryFilters":
        limit = _safe_int(args.get("limit"), settings.default_limit)
        offset = _safe_int(args.get("offset"), 0)
        return cls(
            estado=_clean(args.get("estado")),
            tribunal=_clean(args.get("tribunal")),
            confianca=_clean(args.get("confianca")),
            classe=_clean(args.get("classe")),
            search=_clean(args.get("search")),
            ano=_optional_int(args.get("ano")),
            descoberto_em=_clean(args.get("descoberto_em")),
            offset=max(offset, 0),
            limit=max(1, min(limit, settings.max_limit)),
        )


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _safe_int(value: str | None, fallback: int) -> int:
    try:
        return int(value) if value is not None else fallback
    except (TypeError, ValueError):
        return fallback


def _optional_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None and value.strip() else None
    except (TypeError, ValueError):
        return None


class FileBackedCessaoStore:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._lock = RLock()
        self._snapshot: tuple[tuple[str, int, int], ...] = ()
        self._records: tuple[CessaoRecord, ...] = ()
        self._file_count = 0
        self._publication_count = 0
        self._indices: dict[str, dict[Any, tuple[int, ...]]] = {
            "estado": {},
            "tribunal": {},
            "confianca": {},
            "classe": {},
            "ano": {},
            "descoberto_em": {},
        }
        self._metadata: dict[str, Any] = {}

    def summary(self) -> dict[str, Any]:
        self._refresh_if_needed()
        return {
            "files": self._file_count,
            "publications": self._publication_count,
            "cessoes": len(self._records),
            "database_dir": str(self._data_dir),
        }

    def metadata(self) -> dict[str, Any]:
        self._refresh_if_needed()
        return self._metadata

    def query(self, filters: QueryFilters) -> dict[str, Any]:
        self._refresh_if_needed()
        matched_indexes = self._apply_filters(filters)
        total = len(matched_indexes)
        start = filters.offset
        end = start + filters.limit
        page_indexes = matched_indexes[start:end]

        items = [self._records[index].to_dict() for index in page_indexes]
        return {
            "items": items,
            "pagination": {
                "total": total,
                "offset": filters.offset,
                "limit": filters.limit,
                "returned": len(items),
            },
            "filters": {
                "estado": filters.estado,
                "tribunal": filters.tribunal,
                "confianca": filters.confianca,
                "classe": filters.classe,
                "search": filters.search,
                "ano": filters.ano,
                "descoberto_em": filters.descoberto_em,
            },
        }

    def _refresh_if_needed(self) -> None:
        current_snapshot = self._build_snapshot()
        if current_snapshot == self._snapshot:
            return

        with self._lock:
            current_snapshot = self._build_snapshot()
            if current_snapshot == self._snapshot:
                return
            self._rebuild(current_snapshot)

    def _build_snapshot(self) -> tuple[tuple[str, int, int], ...]:
        if not self._data_dir.exists():
            return ()

        snapshot: list[tuple[str, int, int]] = []
        for file_path in sorted(self._data_dir.glob("*.json")):
            stat = file_path.stat()
            snapshot.append((file_path.name, stat.st_mtime_ns, stat.st_size))
        return tuple(snapshot)

    def _rebuild(self, snapshot: tuple[tuple[str, int, int], ...]) -> None:
        records: list[CessaoRecord] = []
        index_builders: dict[str, defaultdict[Any, list[int]]] = {
            "estado": defaultdict(list),
            "tribunal": defaultdict(list),
            "confianca": defaultdict(list),
            "classe": defaultdict(list),
            "ano": defaultdict(list),
            "descoberto_em": defaultdict(list),
        }
        publication_count = 0
        file_count = 0

        for file_name, _, _ in snapshot:
            file_path = self._data_dir / file_name
            source = infer_source_metadata(file_path)
            file_count += 1

            for publication in iter_json_array(file_path):
                publication_count += 1
                for record in normalize_publication(publication, source):
                    record_index = len(records)
                    records.append(record)
                    self._add_index(index_builders["estado"], record.estado, record_index)
                    self._add_index(
                        index_builders["tribunal"], record.tribunal, record_index
                    )
                    self._add_index(
                        index_builders["confianca"], record.confianca, record_index
                    )
                    self._add_index(index_builders["classe"], record.classe, record_index)
                    self._add_index(index_builders["ano"], record.ano, record_index)
                    self._add_index(
                        index_builders["descoberto_em"], record.descoberto_em, record_index
                    )

        self._records = tuple(records)
        self._publication_count = publication_count
        self._file_count = file_count
        self._snapshot = snapshot
        self._indices = {
            name: {key: tuple(values) for key, values in mapping.items()}
            for name, mapping in index_builders.items()
        }
        self._metadata = self._build_metadata()

    @staticmethod
    def _add_index(index_map: defaultdict[Any, list[int]], key: Any, value: int):
        if key:
            index_map[key].append(value)

    def _apply_filters(self, filters: QueryFilters) -> list[int]:
        candidate_sets: list[set[int]] = []

        if filters.estado:
            candidate_sets.append(set(self._indices["estado"].get(filters.estado, ())))
        if filters.tribunal:
            candidate_sets.append(
                set(self._indices["tribunal"].get(filters.tribunal, ()))
            )
        if filters.confianca:
            candidate_sets.append(
                set(self._indices["confianca"].get(filters.confianca, ()))
            )
        if filters.classe:
            candidate_sets.append(set(self._indices["classe"].get(filters.classe, ())))
        if filters.ano:
            candidate_sets.append(set(self._indices["ano"].get(filters.ano, ())))
        if filters.descoberto_em:
            candidate_sets.append(
                set(self._indices["descoberto_em"].get(filters.descoberto_em, ()))
            )

        if candidate_sets:
            candidate_indexes = set.intersection(*candidate_sets)
        else:
            candidate_indexes = set(range(len(self._records)))

        ordered = sorted(candidate_indexes)
        if not filters.search:
            return ordered

        needle = fold_text(filters.search)
        return [
            index
            for index in ordered
            if needle in self._records[index].search_blob
        ]

    def _build_metadata(self) -> dict[str, Any]:
        def count_values(attribute: str) -> list[dict[str, Any]]:
            counter = Counter(
                getattr(record, attribute)
                for record in self._records
                if getattr(record, attribute)
            )
            return [
                {"value": value, "count": count}
                for value, count in sorted(counter.items(), key=lambda item: item[0])
            ]

        return {
            "totals": {
                "files": self._file_count,
                "publications": self._publication_count,
                "cessoes": len(self._records),
            },
            "estados": count_values("estado"),
            "tribunais": count_values("tribunal"),
            "confiancas": count_values("confianca"),
            "classes": count_values("classe"),
            "anos": count_values("ano"),
            "source_files": sorted({record.source_file for record in self._records}),
        }
