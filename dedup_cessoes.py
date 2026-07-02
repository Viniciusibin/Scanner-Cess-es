"""
dedup_cessoes.py
================
Recebe um JSON já classificado pelo pipeline de cessões e remove duplicatas.
O output mantém a estrutura original do documento (todos os campos),
com classificacoes filtradas apenas para cessões reais não-duplicadas.

Uso:
    python dedup_cessoes.py --input cessoes_classificadas.json
    python dedup_cessoes.py --input cessoes_classificadas.json --output cessoes_unicas.json
    python dedup_cessoes.py --input cessoes_classificadas.json --relatorio --salvar-removidas
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# NORMALIZAÇÃO
# ---------------------------------------------------------------------------
def normalizar_nome(nome: str) -> str:
    if not nome:
        return ""
    nome = nome.lower().strip()
    sufixos = [
        r"\bs\.?a\.?\b", r"\bltda\.?\b", r"\beireli\b", r"\bme\b",
        r"\bepp\b", r"\bs/a\b", r"\bsa\b",
        r"\bfundo de investimento\b", r"\bem direitos creditorios\b",
        r"\bnao padronizados?\b", r"\bnao-padronizados?\b",
        r"\bmultissetorial\b", r"\bresponsabilidade limitada\b",
        r"\bresponsabilidade ltda\b",
    ]
    for s in sufixos:
        nome = re.sub(s, " ", nome)
    nome = re.sub(r"[^\w\s]", " ", nome)
    nome = re.sub(r"\s+", " ", nome).strip()
    return nome


def completude(c: dict) -> int:
    """Pontuação de completude de uma classificação."""
    score = 0
    for campo in ("valor", "classe_credito", "recuperanda", "cedente", "cessionario"):
        if c.get(campo):
            score += 1
    return score


# ---------------------------------------------------------------------------
# DEDUPLICAÇÃO
# ---------------------------------------------------------------------------
def deduplicate(documentos: list[dict], verbose: bool = False) -> tuple[list[dict], list[dict]]:
    """
    Retorna (documentos_unicos, removidas).

    documentos_unicos: lista de documentos no formato original, cada um com
        classificacoes contendo apenas as cessões reais únicas. Documentos
        que ficarem sem nenhuma cessão real após a dedup são descartados.

    removidas: lista de dicts com metadados sobre o que foi removido e porquê.

    Nível 1 — mesmo texto publicado para múltiplos destinatários:
        chave: cnj + md5(texto_completo)

    Nível 2 — mesmo evento de cessão em decisões diferentes:
        chave: cnj_rj + cedente_normalizado + cessionario_normalizado
        Mantém a classificação com maior completude.
    """

    removidas: list[dict] = []

    # ---- Nível 1: deduplicar documentos por texto idêntico ----------------
    # Mantém apenas o primeiro documento com aquele texto.
    seen_texto: dict[str, int] = {}   # chave_texto → id do doc mantido
    docs_nivel1: list[dict] = []

    for doc in documentos:
        cnj   = doc.get("cnj", "")
        texto = doc.get("texto_completo", "")
        hash_texto = hashlib.md5(texto.encode("utf-8", errors="replace")).hexdigest()
        chave_texto = f"{cnj}|{hash_texto}"

        # Filtra apenas cessões reais neste documento
        cessoes_reais = [c for c in doc.get("classificacoes", []) if c.get("is_cessao_real")]
        if not cessoes_reais:
            continue

        if chave_texto in seen_texto:
            for c in cessoes_reais:
                removidas.append({
                    "id_documento":      doc["id"],
                    "cnj":               cnj,
                    "cedente":           c.get("cedente"),
                    "cessionario":       c.get("cessionario"),
                    "cnj_rj":            c.get("cnj_rj"),
                    "recuperanda":       c.get("recuperanda"),
                    "_motivo_remocao":   "nivel1_texto_identico",
                    "_duplicata_de":     seen_texto[chave_texto],
                })
        else:
            seen_texto[chave_texto] = doc["id"]
            # Clona o documento mantendo só cessões reais em classificacoes
            doc_filtrado = {**doc, "classificacoes": cessoes_reais}
            docs_nivel1.append(doc_filtrado)

    if verbose:
        n_removidas_n1 = len([r for r in removidas if r["_motivo_remocao"] == "nivel1_texto_identico"])
        print(f"  Nível 1 (texto idêntico):  {n_removidas_n1:3d} removidos, {len(docs_nivel1):3d} docs restantes")

    # ---- Nível 2: deduplicar por evento de cessão -------------------------
    # Dentro de cada doc, cada classificação é um evento.
    # Entre docs diferentes, colapsamos pelo trio (cnj_rj, cedente, cessionario).
    # Mantemos a classificação com maior completude; o documento que a contém é o que fica.

    # Estrutura: chave_evento → {"doc": doc, "classificacao": c, "doc_idx": int}
    seen_evento: dict[str, dict] = {}

    # Primeiro passo: mapear todos os eventos
    for doc in docs_nivel1:
        for c in doc["classificacoes"]:
            cnj_rj      = (c.get("cnj_rj") or doc.get("cnj") or "").strip()
            cedente     = normalizar_nome(c.get("cedente") or "")
            cessionario = normalizar_nome(c.get("cessionario") or "")

            if not cedente or not cessionario:
                continue  # não deduplica eventos sem partes identificadas

            chave_evento = f"{cnj_rj}|{cedente}|{cessionario}"

            if chave_evento not in seen_evento:
                seen_evento[chave_evento] = {"doc": doc, "classificacao": c}
            else:
                existing = seen_evento[chave_evento]
                if completude(c) > completude(existing["classificacao"]):
                    # O novo é mais completo: marca o existente como removido
                    removidas.append({
                        "id_documento":    existing["doc"]["id"],
                        "cnj":             existing["doc"].get("cnj"),
                        "cedente":         existing["classificacao"].get("cedente"),
                        "cessionario":     existing["classificacao"].get("cessionario"),
                        "cnj_rj":          existing["classificacao"].get("cnj_rj"),
                        "recuperanda":     existing["classificacao"].get("recuperanda"),
                        "_motivo_remocao": "nivel2_mesmo_evento_menos_completo",
                        "_duplicata_de":   doc["id"],
                    })
                    seen_evento[chave_evento] = {"doc": doc, "classificacao": c}
                else:
                    removidas.append({
                        "id_documento":    doc["id"],
                        "cnj":             doc.get("cnj"),
                        "cedente":         c.get("cedente"),
                        "cessionario":     c.get("cessionario"),
                        "cnj_rj":          c.get("cnj_rj"),
                        "recuperanda":     c.get("recuperanda"),
                        "_motivo_remocao": "nivel2_mesmo_evento",
                        "_duplicata_de":   existing["doc"]["id"],
                    })

    # Segundo passo: reconstruir lista de documentos com classificacoes filtradas
    # Um doc só entra no output se tiver pelo menos uma classificação que "ganhou" no nível 2.
    ids_classificacoes_vencedoras: set[tuple] = set()
    for entrada in seen_evento.values():
        ids_classificacoes_vencedoras.add((entrada["doc"]["id"], id(entrada["classificacao"])))

    docs_nivel2: list[dict] = []
    seen_doc_ids: set[int] = set()

    for doc in docs_nivel1:
        classificacoes_vencedoras = []
        for c in doc["classificacoes"]:
            cnj_rj      = (c.get("cnj_rj") or doc.get("cnj") or "").strip()
            cedente     = normalizar_nome(c.get("cedente") or "")
            cessionario = normalizar_nome(c.get("cessionario") or "")

            # Eventos sem partes identificadas sempre ficam (não foram deduplicados)
            if not cedente or not cessionario:
                classificacoes_vencedoras.append(c)
                continue

            chave_evento = f"{cnj_rj}|{cedente}|{cessionario}"
            if seen_evento.get(chave_evento, {}).get("doc", {}).get("id") == doc["id"]:
                classificacoes_vencedoras.append(c)

        if classificacoes_vencedoras and doc["id"] not in seen_doc_ids:
            doc_final = {**doc, "classificacoes": classificacoes_vencedoras}
            docs_nivel2.append(doc_final)
            seen_doc_ids.add(doc["id"])

    if verbose:
        n_removidas_n2 = len([r for r in removidas if r["_motivo_remocao"].startswith("nivel2")])
        print(f"  Nível 2 (mesmo evento):    {n_removidas_n2:3d} removidos, {len(docs_nivel2):3d} docs restantes")

    return docs_nivel2, removidas


# ---------------------------------------------------------------------------
# RELATÓRIO
# ---------------------------------------------------------------------------
def imprimir_relatorio(docs_unicos: list[dict], removidas: list[dict]):
    print("\n" + "=" * 60)
    print("  RELATÓRIO DE DEDUPLICAÇÃO")
    print("=" * 60)

    n1 = [r for r in removidas if r["_motivo_remocao"] == "nivel1_texto_identico"]
    n2 = [r for r in removidas if r["_motivo_remocao"].startswith("nivel2")]

    total_cessoes = sum(len(d.get("classificacoes", [])) for d in docs_unicos)

    print(f"\n  Documentos únicos mantidos: {len(docs_unicos)}")
    print(f"  Cessões únicas mantidas:    {total_cessoes}")
    print(f"  Total removido:             {len(removidas)}")
    print(f"    → Nível 1 (texto igual):  {len(n1)}")
    print(f"    → Nível 2 (mesmo evento): {len(n2)}")

    if n2:
        print("\n  Eventos colapsados (Nível 2):")
        for r in n2:
            print(f"\n    ID {r['id_documento']} → duplicata de {r.get('_duplicata_de')}")
            print(f"    Recuperanda: {r.get('recuperanda')}")
            print(f"    Cedente:     {r.get('cedente')}")
            print(f"    Cessionário: {r.get('cessionario')}")
            print(f"    CNJ_RJ:      {r.get('cnj_rj')}")

    print("\n  Documentos únicos:")
    for doc in docs_unicos:
        for c in doc.get("classificacoes", []):
            print(f"\n    ID {doc['id']} | {c.get('confianca', '?').upper()}")
            print(f"    Recuperanda: {c.get('recuperanda')}")
            print(f"    Cedente:     {c.get('cedente')}")
            print(f"    Cessionário: {c.get('cessionario')}")
            print(f"    CNJ_RJ:      {c.get('cnj_rj')}")
            print(f"    Resumo:      {c.get('resumo')}")

    print("\n" + "=" * 60)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Remove duplicatas de um JSON de cessões já classificadas."
    )
    parser.add_argument("--input",  required=True, help="JSON de entrada")
    parser.add_argument("--output", default=None,  help="JSON de saída (default: <input>_dedup.json)")
    parser.add_argument("--relatorio",      action="store_true", help="Imprime relatório detalhado")
    parser.add_argument("--salvar-removidas", action="store_true", help="Salva <output>_removidas.json")
    args = parser.parse_args()

    if args.output is None:
        stem = Path(args.input).stem
        args.output = str(Path(args.input).parent / f"{stem}_dedup.json")

    with open(args.input, "r", encoding="utf-8") as f:
        dados = json.load(f)

    if isinstance(dados, list):
        documentos = dados
    else:
        print("Erro: o input deve ser uma lista de documentos com campo 'classificacoes'.")
        sys.exit(1)

    total_bruto = sum(
        1 for doc in documentos
        for c in doc.get("classificacoes", [])
        if c.get("is_cessao_real")
    )
    print(f"\nDocumentos no input:         {len(documentos)}")
    print(f"Cessões is_cessao_real=true: {total_bruto}")

    docs_unicos, removidas = deduplicate(documentos, verbose=True)

    if args.relatorio:
        imprimir_relatorio(docs_unicos, removidas)

    # Output: mesma estrutura do input, sem os duplicados
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(docs_unicos, f, ensure_ascii=False, indent=2)
    print(f"\nOutput salvo em: {args.output}")

    if args.salvar_removidas:
        removidas_path = args.output.replace(".json", "_removidas.json")
        with open(removidas_path, "w", encoding="utf-8") as f:
            json.dump(removidas, f, ensure_ascii=False, indent=2)
        print(f"Removidas salvas em: {removidas_path}")


if __name__ == "__main__":
    main()