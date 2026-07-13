"""
Download dos cadernos DJEN mais recentes + scan de cessões, versão standalone
para monitoramento diário. Não depende de nenhum arquivo fora desta pasta
(monitoramento-diario) — só usa scan_cessoes.py, que está ao lado.

Para cada tribunal, tenta baixar o caderno dos últimos `dias_lookback` dias
(cobre fins de semana/feriados sem caderno). Dias já processados com sucesso
(baixados e escaneados) são pulados nas próximas execuções — controlado por
dados/DJEN-<TRIBUNAL>/processados.json.

  - Encontrou match  -> mantém o json do diário + matches_<data>.json
  - Sem match        -> apaga o zip e o json do diário (não guarda lixo)
"""

import json
import shutil
import sys
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from scan_cessoes import scan_diretorio  # noqa: E402

API_URL = "https://comunicaapi.pje.jus.br/api/v1/caderno/{tribunal}/{data}/{meio}"

DADOS_DIR = SCRIPT_DIR / "dados"


def _processados_path(tribunal: str) -> Path:
    return DADOS_DIR / f"DJEN-{tribunal}" / "processados.json"


def _carregar_processados(tribunal: str) -> set:
    p = _processados_path(tribunal)
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def _marcar_processado(tribunal: str, data_str: str, processados: set) -> None:
    processados.add(data_str)
    p = _processados_path(tribunal)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(sorted(processados), f, ensure_ascii=False, indent=2)


def baixar_e_escanear_dia(tribunal: str, data: date, meio: str = "D") -> dict:
    """Baixa e escaneia o caderno de um único dia. Retorna um resumo do resultado."""
    data_str = data.strftime("%Y-%m-%d")
    base_dir = DADOS_DIR / f"DJEN-{tribunal}"
    mes_dir = base_dir / f"{data.year:04d}-{data.month:02d}"
    dir_dia = mes_dir / data_str
    zip_file = mes_dir / f"{data_str}.zip"

    mes_dir.mkdir(parents=True, exist_ok=True)

    try:
        resp = requests.get(API_URL.format(tribunal=tribunal, data=data_str, meio=meio), timeout=30)
        resp.raise_for_status()
        info = resp.json()
    except Exception:
        return {"data": data_str, "status": "sem_caderno"}

    url = info.get("url")
    if not url:
        return {"data": data_str, "status": "url_vazia"}

    try:
        with requests.get(url, timeout=120, stream=True) as r:
            r.raise_for_status()
            with open(zip_file, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)

        dir_dia.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_file) as z:
            z.extractall(dir_dia)
    except Exception as e:
        if zip_file.exists():
            zip_file.unlink()
        if dir_dia.exists():
            shutil.rmtree(dir_dia)
        return {"data": data_str, "status": f"erro_download: {e}"}

    zip_file.unlink()  # já extraído, não precisa mais dele

    resultados = scan_diretorio(str(dir_dia))

    # Jsons brutos já foram escaneados - só o resultado do match interessa
    for raw_json in dir_dia.rglob("*.json"):
        raw_json.unlink()

    if resultados:
        matches_file = dir_dia / f"matches_{data_str}.json"
        with open(matches_file, "w", encoding="utf-8") as f:
            json.dump(resultados, f, ensure_ascii=False, indent=2)
        return {
            "data": data_str,
            "status": "match",
            "n_matches": len(resultados),
            "matches_file": str(matches_file),
        }

    shutil.rmtree(dir_dia)
    return {"data": data_str, "status": "sem_match"}


def baixar_e_escanear_recente(tribunal: str, dias_lookback: int = 5, meio: str = "D") -> list:
    """
    Tenta baixar e escanear os cadernos dos últimos `dias_lookback` dias (hoje incluso).
    Dias já processados com sucesso em execuções anteriores são pulados sem chamar a API.
    """
    processados = _carregar_processados(tribunal)
    hoje = date.today()
    resumos = []

    for i in range(dias_lookback):
        data = hoje - timedelta(days=i)
        data_str = data.strftime("%Y-%m-%d")

        if data_str in processados:
            print(f"[{tribunal}] {data_str} ja_processado")
            resumos.append({"data": data_str, "status": "ja_processado"})
            continue

        print(f"[{tribunal}] {data_str} ...", end=" ", flush=True)
        resumo = baixar_e_escanear_dia(tribunal, data, meio=meio)
        print(resumo["status"])
        resumos.append(resumo)

        # Só marca como processado quando o download/scan foi concluído com sucesso.
        # sem_caderno / erro_download ficam livres para tentar de novo na próxima execução.
        if resumo["status"] in ("match", "sem_match"):
            _marcar_processado(tribunal, data_str, processados)

        time.sleep(0.5)

    return resumos


if __name__ == "__main__":
    tribunal = sys.argv[1] if len(sys.argv) > 1 else "TJMT"
    dias = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    baixar_e_escanear_recente(tribunal, dias_lookback=dias)
