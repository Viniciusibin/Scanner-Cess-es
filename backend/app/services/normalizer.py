from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

from ..domain.models import CessaoRecord, SourceFileMetadata


NON_DIGIT_RE = re.compile(r"[^\d,.-]+")

TEXT_REPLACEMENTS = {
    "RECUPERAçãO": "RECUPERAÇÃO",
    "IMPUGNAçãO": "IMPUGNAÇÃO",
    "CRéDITO": "CRÉDITO",
    "FALêNCIA": "FALÊNCIA",
    "EMPRESáRIOS": "EMPRESÁRIOS",
    "EMPRESáRIAIS": "EMPRESÁRIAIS",
    "PRESERVAçãO": "PRESERVAÇÃO",
    "LOCAçõES": "LOCAÇÕES",
    "Civel": "Cível",
}


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    for source, target in TEXT_REPLACEMENTS.items():
        text = text.replace(source, target)
    return text or None


def fold_text(value: Any) -> str:
    text = normalize_text(value) or ""
    normalized = unicodedata.normalize("NFKD", text)
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return without_accents.casefold()


def normalize_money(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)

    raw = NON_DIGIT_RE.sub("", str(value))
    if not raw:
        return None

    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")

    try:
        return float(raw)
    except ValueError:
        return None


def normalize_classe_credito(value: Any) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    return text.upper()


DATA_CESSAO_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def normalize_data_cessao(value: Any) -> str | None:
    """Converte 'DD/MM/AAAA' para ISO 'AAAA-MM-DD'. Retorna None se nao for
    uma data valida nesse formato (ex.: "Nao encontrado", vazio, None)."""
    text = normalize_text(value)
    if not text:
        return None
    match = DATA_CESSAO_RE.match(text)
    if not match:
        return None
    day, month, year = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


def infer_source_metadata(file_path: Path) -> SourceFileMetadata:
    stem = file_path.stem.upper()
    match = re.match(r"(?P<prefix>[A-Z]{2})-(?P<state>[A-Z]{2})-(?P<year>\d{4})", stem)
    if match:
        prefix = match.group("prefix")
        state = match.group("state")
        tribunal = f"{prefix}{state}"
        year = int(match.group("year"))
        return SourceFileMetadata(
            file_name=file_path.name,
            state=state,
            tribunal=tribunal,
            year=year,
        )

    return SourceFileMetadata(
        file_name=file_path.name,
        state="NA",
        tribunal="NA",
        year=None,
    )


def _extract_classificacoes(publication: dict[str, Any]) -> list[dict[str, Any]]:
    raw_list = publication.get("classificacoes")
    if isinstance(raw_list, list):
        return [item for item in raw_list if isinstance(item, dict)]

    raw_single = publication.get("classificacao")
    if isinstance(raw_single, dict):
        return [raw_single]

    return []


def _search_blob(parts: Iterable[Any]) -> str:
    return " ".join(part for part in (fold_text(part) for part in parts) if part)


def normalize_publication(
    publication: dict[str, Any],
    source: SourceFileMetadata,
) -> list[CessaoRecord]:
    normalized_rows: list[CessaoRecord] = []
    base_publication_id = publication.get("id")
    publication_cnj = normalize_text(publication.get("cnj"))
    publication_class = normalize_text(publication.get("classe"))
    publication_orgao = normalize_text(publication.get("orgao"))
    publication_data = normalize_text(publication.get("data"))
    publication_link = normalize_text(publication.get("link"))
    publication_text = normalize_text(publication.get("texto_completo"))
    publication_valor_causa = normalize_money(publication.get("valor_causa"))
    publication_data_cessao = normalize_data_cessao(publication.get("data_cessao_credito"))

    for index, classificacao in enumerate(_extract_classificacoes(publication)):
        if not classificacao.get("is_cessao_real"):
            continue

        cedente = normalize_text(classificacao.get("cedente"))
        cessionario = normalize_text(classificacao.get("cessionario"))
        cnj = normalize_text(classificacao.get("cnj_rj")) or publication_cnj
        recuperanda = normalize_text(classificacao.get("recuperanda"))
        confianca = normalize_text(classificacao.get("confianca"))
        resumo = normalize_text(classificacao.get("resumo"))
        motivo = normalize_text(classificacao.get("motivo_classificacao"))
        data_cessao = normalize_data_cessao(classificacao.get("data_cessao")) or publication_data_cessao

        record_id = (
            f"{base_publication_id or source.file_name}-{index}-"
            f"{cedente or 'sem-cedente'}-{cessionario or 'sem-cessionario'}"
        )

        normalized_rows.append(
            CessaoRecord(
                id=record_id,
                publication_id=base_publication_id,
                estado=source.state,
                tribunal=normalize_text(publication.get("tribunal")) or source.tribunal,
                data=publication_data,
                ano=source.year,
                data_cessao=data_cessao,
                cnj=cnj,
                classe=publication_class,
                orgao=publication_orgao,
                recuperanda=recuperanda,
                cedente=cedente,
                cessionario=cessionario,
                valor=normalize_money(classificacao.get("valor")),
                valor_causa=publication_valor_causa,
                classe_credito=normalize_classe_credito(
                    classificacao.get("classe_credito")
                ),
                confianca=confianca,
                resumo=resumo,
                motivo=motivo,
                texto_completo=publication_text,
                link=publication_link,
                source_file=source.file_name,
                search_blob=_search_blob(
                    [
                        source.state,
                        source.tribunal,
                        cnj,
                        publication_class,
                        publication_orgao,
                        recuperanda,
                        cedente,
                        cessionario,
                        confianca,
                        resumo,
                        motivo,
                    ]
                ),
            )
        )

    return normalized_rows
