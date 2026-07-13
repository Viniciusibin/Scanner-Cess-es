from __future__ import annotations

import sys
import threading
from datetime import datetime, timezone
from typing import Any

from ..core.config import PROJECT_ROOT

DIARIO_DIR = PROJECT_ROOT / "diario"
if str(DIARIO_DIR) not in sys.path:
    sys.path.insert(0, str(DIARIO_DIR))

TRIBUNAIS_PADRAO = [
    "TJBA", "TJGO", "TJMG", "TJMS", "TJMT",
    "TJPE", "TJPR", "TJRJ", "TJRS", "TJSP", "TJTO",
]


class DiarioRunner:
    """Dispara o monitoramento diário (diario/monitorar_diario.py) numa
    thread em background e guarda o estado da execução em memória.

    Limitação conhecida: o estado vive em memória de processo, então só
    funciona de forma confiável com 1 worker (uso local ou gunicorn -w 1) —
    suficiente para um botão manual de uso interno.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "running": False,
            "started_at": None,
            "finished_at": None,
            "resumo_por_tribunal": {},
            "erro": None,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def iniciar(self, tribunais: list[str] | None = None, dias_lookback: int | None = None) -> bool:
        """Dispara a execução numa thread em background. Retorna False sem
        fazer nada se já tiver uma execução em andamento."""
        with self._lock:
            if self._state["running"]:
                return False
            self._state = {
                "running": True,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
                "resumo_por_tribunal": {},
                "erro": None,
            }

        thread = threading.Thread(
            target=self._executar,
            args=(tribunais or TRIBUNAIS_PADRAO, dias_lookback),
            daemon=True,
        )
        thread.start()
        return True

    def _atualizar_progresso(self, tribunal: str, mensagem: str) -> None:
        with self._lock:
            resumo = self._state["resumo_por_tribunal"]
            resumo[tribunal] = {**resumo.get(tribunal, {}), "status": mensagem}

    def _executar(self, tribunais: list[str], dias_lookback: int | None) -> None:
        try:
            from monitorar_diario import executar_diario  # import tardio - depende do sys.path acima

            resultado = executar_diario(
                tribunais, dias_lookback=dias_lookback, on_progress=self._atualizar_progresso
            )
            with self._lock:
                self._state["resumo_por_tribunal"] = resultado
        except Exception as exc:  # noqa: BLE001 - reportar no status em vez de derrubar a thread
            with self._lock:
                self._state["erro"] = str(exc)
        finally:
            with self._lock:
                self._state["running"] = False
                self._state["finished_at"] = datetime.now(timezone.utc).isoformat()
