"""Fixtures dos testes. O SCRIPT_EXPLICA.py é importado de uma CÓPIA numa pasta
temporária (os JSONs/logs que ele cria ficam lá) e com %LOCALAPPDATA% isolado
(perfil do Chrome, drivers e cache de áudio de teste não tocam no PC real).
Nenhum teste aqui acessa o Feegow."""
import importlib.util
import os
import shutil
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session")
def pasta_teste(tmp_path_factory):
    pasta = tmp_path_factory.mktemp("monitor")
    os.makedirs(pasta / "localappdata", exist_ok=True)
    os.environ["LOCALAPPDATA"] = str(pasta / "localappdata")
    for nome in ("SCRIPT_EXPLICA.py", "filtros.json", "iniciar_monitor.pyw", "versao.json"):
        shutil.copy(os.path.join(RAIZ, nome), pasta / nome)
    return pasta


def _importar(caminho, nome):
    from importlib.machinery import SourceFileLoader
    carregador = SourceFileLoader(nome, str(caminho))
    spec = importlib.util.spec_from_loader(nome, carregador)
    modulo = importlib.util.module_from_spec(spec)
    carregador.exec_module(modulo)
    return modulo


@pytest.fixture(scope="session")
def monitor(pasta_teste):
    """O módulo do monitor (uma importação por sessão: cria uma janela Tk oculta)."""
    os.environ.pop("MONITOR_SUPERVISOR", None)
    return _importar(pasta_teste / "SCRIPT_EXPLICA.py", "monitor_teste")


@pytest.fixture
def supervisor(pasta_teste):
    return _importar(pasta_teste / "iniciar_monitor.pyw", "supervisor_teste")


class SaidaInterceptada(BaseException):
    pass


@pytest.fixture
def saidas(monkeypatch, monitor):
    """Troca os._exit e relancar_processo_agora por gravadores."""
    chamadas = []

    def saida_falsa(codigo):
        chamadas.append(("exit", codigo))
        raise SaidaInterceptada(codigo)
    monkeypatch.setattr(os, "_exit", saida_falsa)
    monkeypatch.setattr(monitor, "relancar_processo_agora", lambda: chamadas.append(("relancar",)))
    return chamadas


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
