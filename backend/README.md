# Backend Flask

Backend preparado para servir o frontend a partir dos arquivos JSON em `../database`.

## Principios da estrutura

- `app/__init__.py`: app factory e bootstrap do Flask.
- `app/api/`: camada HTTP fina, sem regra de negocio.
- `app/services/json_loader.py`: leitura incremental dos JSONs com `ijson`.
- `app/services/normalizer.py`: normalizacao defensiva do schema bruto.
- `app/services/cessao_store.py`: cache em memoria, indices e recarga automatica.
- `app/domain/models.py`: contratos internos imutaveis.

## Como subir

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

## Endpoints

- `GET /api/v1/health`
- `GET /api/v1/meta`
- `GET /api/v1/cessoes`

## Filtros

`/api/v1/cessoes?estado=MS&tribunal=TJMS&confianca=alta&search=travessia&ano=2024&limit=50&offset=0`

## Observacoes de escalabilidade

- Os arquivos sao lidos apenas quando ha mudanca de `mtime` ou tamanho.
- O parser usa streaming para evitar carregar o JSON inteiro quando o dataset crescer.
- O schema bruto fica isolado do frontend; a API sempre devolve um formato normalizado.
- Se o volume crescer muito alem da memoria disponivel, o proximo passo natural e trocar o store por SQLite/PostgreSQL sem quebrar a API.
