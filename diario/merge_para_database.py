"""
Elo entre o monitoramento diário e o database/: pega as cessões reais do dia
(já classificadas por classificar_cessoes.py), aplica a mesma lógica de
filtro_cessoes.py (UPEFAZ + dedup + relevância), enriquece via
pdpj_capa.py (recuperanda) e pdpj_valor_causa.py (valor da causa), e faz o
merge idempotente (por "id") em database/TJ-<UF>-<ano>.json.

Uso:
    from merge_para_database import merge_cessoes_reais
    resumo = merge_cessoes_reais("TJSP", cessoes_reais)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

from filtro_cessoes import build_dedup_key, count_filled_fields, is_institutional, is_upefaz  # noqa: E402
import pdpj_capa  # noqa: E402
import pdpj_valor_causa  # noqa: E402

DATABASE_DIR = REPO_ROOT / "database"


# ============================================================
# 1. filtro_cessoes.py — UPEFAZ + dedup + relevância
# ============================================================

def _aplicar_filtro_cessoes(publicacoes: list[dict]) -> list[dict]:
    after_upefaz = [r for r in publicacoes if not is_upefaz(r)]

    seen_keys: dict[str, dict] = {}
    no_key: list[dict] = []
    for r in after_upefaz:
        key = build_dedup_key(r)
        if key is None:
            no_key.append(r)
            continue
        if key in seen_keys:
            if count_filled_fields(r) > count_filled_fields(seen_keys[key]):
                seen_keys[key] = r
        else:
            seen_keys[key] = r

    resultado = list(seen_keys.values()) + no_key
    for r in resultado:
        r["relevancia_btg"] = "alta" if is_institutional(r) else "baixa"
    return resultado


# ============================================================
# 2. pdpj_capa.py — recuperanda via capa dos autos (não-interativo)
# ============================================================

def _carregar_access_token_nao_interativo() -> str | None:
    """Versão não-interativa de pdpj_capa.carregar_access_token(): só tenta
    renovar via refresh_token ou usar o access_token salvo; nunca chama
    input() (isso travaria uma thread em background do Flask)."""
    if not os.path.exists(pdpj_capa.TOKEN_FILE):
        return None

    with open(pdpj_capa.TOKEN_FILE, encoding="utf-8") as f:
        dados = json.load(f)

    refresh_token = dados.get("refresh_token", "")
    if refresh_token and not pdpj_capa._token_expirado(refresh_token):
        novos = pdpj_capa._renovar_token(refresh_token)
        if novos and novos.get("access_token"):
            dados.update(novos)
            with open(pdpj_capa.TOKEN_FILE, "w", encoding="utf-8") as f:
                json.dump(dados, f, ensure_ascii=False, indent=2)
            return novos["access_token"]

    access_token = dados.get("access_token", "")
    if access_token and not pdpj_capa._token_expirado(access_token):
        return access_token

    return None


def _enriquecer_recuperanda(publicacoes: list[dict]) -> str | None:
    """Preenche 'recuperanda' vazio via PDPJ, só para os CNJs desta leva.
    Retorna uma mensagem de aviso se o token da PDPJ não estiver disponível
    (o resto do merge continua normalmente, só sem esse enriquecimento)."""
    pendentes: list[tuple[str, dict]] = []
    for pub in publicacoes:
        for classificacao in pub.get("classificacoes", []):
            if not classificacao.get("is_cessao_real"):
                continue
            if (classificacao.get("recuperanda") or "").strip():
                continue
            cnj = (classificacao.get("cnj_rj") or pub.get("cnj") or "").strip()
            if cnj:
                pendentes.append((cnj, classificacao))

    if not pendentes:
        return None

    token = _carregar_access_token_nao_interativo()
    if not token:
        return "Token PDPJ expirado — rode `python pdpj_capa.py` manualmente para renovar (recuperanda não enriquecida nesta rodada)."

    pdpj_capa.ACCESS_TOKEN = token
    pdpj_capa.HEADERS = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    cache: dict[str, dict] = {}
    for cnj, classificacao in pendentes:
        if cnj not in cache:
            cache[cnj] = pdpj_capa.processar_cnj_capa(cnj)
            pdpj_capa._salvar_saida_cnj(cnj, cache[cnj])
        resultado = cache[cnj]

        if resultado.get("status") != "ok":
            continue
        escolha = pdpj_capa.escolher_recuperanda(resultado.get("polos") or {}, resultado.get("classe"))
        if escolha:
            classificacao["recuperanda"] = escolha
            classificacao["recuperanda_fonte"] = pdpj_capa.RECUPERANDA_FONTE

    return None


# ============================================================
# 3. pdpj_valor_causa.py — valor da causa via capa dos autos
# ============================================================

def _enriquecer_valor_causa(publicacoes: list[dict]) -> None:
    cache: dict[str, float | None] = {}
    for pub in publicacoes:
        cnj = pub.get("cnj")
        if not cnj:
            continue
        if cnj not in cache:
            try:
                resultado = pdpj_valor_causa.buscar_valor_causa(cnj)
                cache[cnj] = resultado.get("valor")
            except Exception:  # noqa: BLE001 - falha pontual não deve travar o merge
                cache[cnj] = None
        pub["valor_causa"] = cache[cnj]


# ============================================================
# 4. Merge idempotente em database/TJ-<UF>-<ano>.json
# ============================================================

def _uf_do_tribunal(tribunal: str) -> str:
    return tribunal[-2:].upper()


def _arquivo_database(tribunal: str, ano: int) -> Path:
    return DATABASE_DIR / f"TJ-{_uf_do_tribunal(tribunal)}-{ano}.json"


def _ano_da_publicacao(pub: dict) -> int:
    # "data" é preenchida por monitorar_diario.py no formato ISO "AAAA-MM-DD".
    data = pub.get("data") or ""
    partes_iso = data.split("-")
    if len(partes_iso) == 3 and partes_iso[0].isdigit():
        return int(partes_iso[0])
    # Fallback defensivo, caso algum dia "data" venha como "DD/MM/AAAA".
    partes_br = data.split("/")
    if len(partes_br) == 3 and partes_br[2].isdigit():
        return int(partes_br[2])
    return date.today().year


def _merge_arquivo(caminho: Path, novas_publicacoes: list[dict]) -> int:
    """Merge idempotente por 'id'. Retorna quantas publicações foram
    efetivamente adicionadas (ids já existentes no arquivo são ignorados)."""
    if caminho.exists():
        with open(caminho, encoding="utf-8") as f:
            existentes = json.load(f)
    else:
        existentes = []

    ids_existentes = {item.get("id") for item in existentes if item.get("id") is not None}
    adicionadas = [pub for pub in novas_publicacoes if pub.get("id") not in ids_existentes]

    if not adicionadas:
        return 0

    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(existentes + adicionadas, f, ensure_ascii=False, indent=2)
    os.replace(tmp, caminho)

    return len(adicionadas)


def merge_cessoes_reais(tribunal: str, cessoes_reais: list[dict]) -> dict:
    """Ponto de entrada chamado por monitorar_diario.py para cada tribunal.
    Retorna um resumo {publicadas, novas_no_database, aviso}."""
    if not cessoes_reais:
        return {"publicadas": 0, "novas_no_database": 0, "aviso": None}

    filtradas = _aplicar_filtro_cessoes(cessoes_reais)

    aviso = _enriquecer_recuperanda(filtradas)
    _enriquecer_valor_causa(filtradas)

    por_ano: dict[int, list[dict]] = {}
    for pub in filtradas:
        por_ano.setdefault(_ano_da_publicacao(pub), []).append(pub)

    novas_no_database = 0
    for ano, publicacoes in por_ano.items():
        novas_no_database += _merge_arquivo(_arquivo_database(tribunal, ano), publicacoes)

    return {
        "publicadas": len(filtradas),
        "novas_no_database": novas_no_database,
        "aviso": aviso,
    }
