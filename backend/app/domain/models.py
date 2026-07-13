from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceFileMetadata:
    file_name: str
    state: str
    tribunal: str
    year: int | None


@dataclass(frozen=True, slots=True)
class CessaoRecord:
    id: str
    publication_id: int | str | None
    estado: str
    tribunal: str
    data: str | None
    ano: int | None
    data_cessao: str | None
    descoberto_em: str | None
    cnj: str | None
    classe: str | None
    orgao: str | None
    recuperanda: str | None
    cedente: str | None
    cessionario: str | None
    valor: float | None
    valor_causa: float | None
    classe_credito: str | None
    confianca: str | None
    resumo: str | None
    motivo: str | None
    texto_completo: str | None
    link: str | None
    source_file: str
    search_blob: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "publication_id": self.publication_id,
            "estado": self.estado,
            "tribunal": self.tribunal,
            "data": self.data,
            "ano": self.ano,
            "data_cessao": self.data_cessao,
            "descoberto_em": self.descoberto_em,
            "cnj": self.cnj,
            "classe": self.classe,
            "orgao": self.orgao,
            "recuperanda": self.recuperanda,
            "cedente": self.cedente,
            "cessionario": self.cessionario,
            "valor": self.valor,
            "valor_causa": self.valor_causa,
            "classe_credito": self.classe_credito,
            "confianca": self.confianca,
            "resumo": self.resumo,
            "motivo": self.motivo,
            "texto_completo": self.texto_completo,
            "link": self.link,
            "source_file": self.source_file,
        }
