#!/usr/bin/env python3
"""
filtrar_cessoes.py — Camada 4 do DJEN Scanner
Pós-processamento dos JSONs classificados (output da Camada 3).

Filtros aplicados em cascata:
  1. UPEFAZ        — remove publicações da UPEFAZ / Execuções contra a Fazenda Pública
  2. DEDUP         — remove duplicatas onde CNJ + cedente + cessionário são idênticos
  3. RELEVÂNCIA    — marca cada registro como relevancia_btg "alta" ou "baixa"
                     (alta = pelo menos um lado institucional)

Output:
  - JSON filtrado (mesma estrutura + campo relevancia_btg, descartados removidos)
  - Relatório de stats no terminal

Uso:
  python filtrar_cessoes.py <input.json> [--output <output.json>] [--somente-alta]
  python filtrar_cessoes.py database/TJ-SP-2026.json
  python filtrar_cessoes.py database/TJ-SP-2026.json --somente-alta
  python filtrar_cessoes.py database/ --output database/filtrado/  # processa todos os .json da pasta
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from collections import defaultdict


# =============================================================================
# CONFIGURAÇÃO DOS FILTROS
# =============================================================================

# Filtro 1 — UPEFAZ: substrings no campo "orgao" que indicam execução contra a Fazenda
UPEFAZ_PATTERNS = [
    "UPEFAZ",
    "Execuções contra a Fazenda Pública",
    "Execucoes contra a Fazenda Publica",
    "Unidade de Processamento das Execuções contra a Fazenda",
    "Unidade de Processamento das Execucoes contra a Fazenda",
]

# Filtro 3 — Padrões de cessionários/cedentes institucionais (case-insensitive)
# Inclui: FIDCs, securitizadoras, bancos, gestoras, fundos, players conhecidos
INSTITUTIONAL_PATTERNS = re.compile(
    r"(?i)"
    r"(?:"
    # Termos genéricos
    r"fidc"
    r"|fundo de investimento"
    r"|fundo de investimentos"
    r"|securitizadora"
    r"|banco\b"
    r"|banrisul"
    r"|bradesco"
    r"|santander"
    r"|ita[úu]\b"
    r"|caixa econ[oô]mica"
    r"|banco do brasil"
    r"|gestora"
    r"|recuperadora de cr[eé]ditos"
    # Players conhecidos do mercado distressed
    r"|lepta"
    r"|travessia"
    r"|priority"
    r"|sorachi"
    r"|kwar[aá]"
    r"|kuara"
    r"|okno"
    r"|insol"
    r"|red fundo"
    r"|iox\b"
    r"|creditum"
    r"|ativos s\.?a"
    r"|strata"
    r"|jive"
    r"|enforce"
    r"|lutece"
    r"|lut[eè]ce"
    r"|des sables"
    r"|dtom"
    r"|conexcred"
    r"|pcg.+brasil.+multicarteira"
    r"|npl"
    r"|distressed"
    r"|special situations"
    r"|credores fidc"
    r"|lotus performance"
    r"|acreditar fundo"
    r"|multiplike"
    r"|libra.+fidc"
    r"|sdg fundo"
    r"|sb cr[eé]dito"
    r"|everblue"
    r"|raio participa"
    r"|paulista distressed"
    r"|dsa participa"
    r")"
)


# =============================================================================
# FUNÇÕES DE FILTRO
# =============================================================================

def is_upefaz(record: dict) -> bool:
    """Retorna True se a publicação é da UPEFAZ."""
    orgao = (record.get("orgao") or "").strip()
    orgao_lower = orgao.lower()
    for pattern in UPEFAZ_PATTERNS:
        if pattern.lower() in orgao_lower:
            return True
    return False


def normalize_for_dedup(value: str | None) -> str:
    """Normaliza string para comparação de dedup."""
    if not value:
        return ""
    # Lowercase, remove pontuação e espaços extras
    v = value.lower().strip()
    v = re.sub(r"[.\-/,;:()'\"]", "", v)
    v = re.sub(r"\s+", " ", v)
    return v


def build_dedup_key(record: dict) -> str | None:
    """
    Constrói chave de deduplicação: CNJ + cedente + cessionário (normalizados).
    Retorna None se não tem CNJ (não pode deduplicar).
    Usa a PRIMEIRA classificação do registro.
    """
    cnj = normalize_for_dedup(record.get("cnj"))
    if not cnj:
        return None

    classificacoes = record.get("classificacoes", [])
    if not classificacoes:
        return None

    c = classificacoes[0]
    cedente = normalize_for_dedup(c.get("cedente"))
    cessionario = normalize_for_dedup(c.get("cessionario"))

    # Só deduplica se tem pelo menos cedente OU cessionário
    if not cedente and not cessionario:
        return None

    return f"{cnj}|{cedente}|{cessionario}"


def count_filled_fields(record: dict) -> int:
    """Conta campos preenchidos nas classificações — usado para decidir qual duplicata manter."""
    score = 0
    for c in record.get("classificacoes", []):
        for field in ["cedente", "cessionario", "valor", "classe_credito", "cnj_rj", "recuperanda"]:
            if c.get(field):
                score += 1
    return score


def is_institutional(record: dict) -> bool:
    """Retorna True se pelo menos um cedente OU cessionário é institucional."""
    for c in record.get("classificacoes", []):
        cedente = c.get("cedente") or ""
        cessionario = c.get("cessionario") or ""
        combined = f"{cedente} {cessionario}"
        if INSTITUTIONAL_PATTERNS.search(combined):
            return True

    # Fallback: checa também nos destinatários (às vezes o classificador não extrai)
    for dest in record.get("destinatarios", []):
        if INSTITUTIONAL_PATTERNS.search(dest):
            return True

    return False


# =============================================================================
# PIPELINE PRINCIPAL
# =============================================================================

def process_file(input_path: str, only_alta: bool = False) -> tuple[list[dict], dict]:
    """
    Processa um JSON de cessões classificadas.
    Retorna (registros_filtrados, stats).
    """
    with open(input_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    # Se o JSON é um dict com uma chave que contém a lista, tenta extrair
    if isinstance(records, dict):
        # Tenta chaves comuns
        for key in ["cessoes", "results", "data", "matches"]:
            if key in records and isinstance(records[key], list):
                records = records[key]
                break
        else:
            # Se é dict mas não tem lista óbvia, tenta converter
            if isinstance(records, dict):
                records = [records]

    if not isinstance(records, list):
        print(f"  ERRO: formato inesperado em {input_path}", file=sys.stderr)
        return [], {}

    total = len(records)
    stats = {
        "total_input": total,
        "removido_upefaz": 0,
        "removido_dedup": 0,
        "output_alta": 0,
        "output_baixa": 0,
        "output_total": 0,
    }

    # --- Filtro 1: UPEFAZ ---
    after_upefaz = []
    for r in records:
        if is_upefaz(r):
            stats["removido_upefaz"] += 1
        else:
            after_upefaz.append(r)

    # --- Filtro 2: Dedup por CNJ + cedente + cessionário ---
    seen_keys: dict[str, dict] = {}  # key -> melhor registro
    no_key = []  # registros sem chave de dedup (passam direto)

    for r in after_upefaz:
        key = build_dedup_key(r)
        if key is None:
            no_key.append(r)
            continue

        if key in seen_keys:
            existing = seen_keys[key]
            # Mantém o que tem mais campos preenchidos
            if count_filled_fields(r) > count_filled_fields(existing):
                seen_keys[key] = r
            stats["removido_dedup"] += 1
        else:
            seen_keys[key] = r

    after_dedup = list(seen_keys.values()) + no_key

    # --- Filtro 3: Relevância institucional ---
    output = []
    for r in after_dedup:
        if is_institutional(r):
            r["relevancia_btg"] = "alta"
            stats["output_alta"] += 1
        else:
            r["relevancia_btg"] = "baixa"
            stats["output_baixa"] += 1

        if only_alta and r["relevancia_btg"] != "alta":
            continue

        output.append(r)

    stats["output_total"] = len(output)

    return output, stats


def print_stats(stats: dict, filename: str):
    """Imprime relatório de stats para um arquivo."""
    total = stats["total_input"]
    if total == 0:
        print(f"\n  {filename}: vazio")
        return

    pct = lambda n: f"{n/total*100:.1f}%" if total > 0 else "0%"

    print(f"\n  {'─'*60}")
    print(f"  {filename}")
    print(f"  {'─'*60}")
    print(f"  Input total:            {total:>6}")
    print(f"  Removido (UPEFAZ):      {stats['removido_upefaz']:>6}  ({pct(stats['removido_upefaz'])})")
    print(f"  Removido (dedup):       {stats['removido_dedup']:>6}  ({pct(stats['removido_dedup'])})")
    print(f"  Output total:           {stats['output_total']:>6}  ({pct(stats['output_total'])})")
    print(f"    → relevância alta:    {stats['output_alta']:>6}  ({pct(stats['output_alta'])})")
    print(f"    → relevância baixa:   {stats['output_baixa']:>6}  ({pct(stats['output_baixa'])})")
    print(f"  {'─'*60}")


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Camada 4 — Filtragem de cessões classificadas pelo DJEN Scanner"
    )
    parser.add_argument(
        "input",
        help="Arquivo .json ou pasta com .jsons para processar"
    )
    parser.add_argument(
        "--output", "-o",
        help="Arquivo ou pasta de output (default: <input>_filtrado.json ou pasta_filtrado/)"
    )
    parser.add_argument(
        "--somente-alta",
        action="store_true",
        help="Incluir apenas registros com relevancia_btg='alta' no output"
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    only_alta = args.somente_alta

    # Determina se é arquivo ou pasta
    if input_path.is_file():
        files = [input_path]
        if args.output:
            output_path = Path(args.output)
        else:
            output_path = input_path.with_name(input_path.stem + "_filtrado.json")
        output_is_dir = False
    elif input_path.is_dir():
        files = sorted(input_path.glob("*.json"))
        if not files:
            print(f"Nenhum .json encontrado em {input_path}", file=sys.stderr)
            sys.exit(1)
        if args.output:
            output_dir = Path(args.output)
        else:
            output_dir = input_path / "filtrado"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_is_dir = True
    else:
        print(f"Input não encontrado: {input_path}", file=sys.stderr)
        sys.exit(1)

    # Processa
    grand_stats = defaultdict(int)

    for fp in files:
        print(f"\nProcessando: {fp.name} ...")
        output_records, stats = process_file(str(fp), only_alta)
        print_stats(stats, fp.name)

        # Acumula stats globais
        for k, v in stats.items():
            grand_stats[k] += v

        # Salva output
        if output_is_dir:
            out_fp = output_dir / (fp.stem + "_filtrado.json")
        else:
            out_fp = output_path

        with open(str(out_fp), "w", encoding="utf-8") as f:
            json.dump(output_records, f, ensure_ascii=False, indent=2)

        print(f"  Salvo em: {out_fp}")

    # Stats globais (se mais de 1 arquivo)
    if len(files) > 1:
        print_stats(dict(grand_stats), "TOTAL (todos os arquivos)")

    print(f"\nConcluído. {grand_stats.get('output_total', 0)} cessões no output final.")


if __name__ == "__main__":
    main()