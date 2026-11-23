"""Fala por filtro (2.2.0): texto próprio, voz liga/desliga, migração do filtros.json
antigo e compatibilidade. Nada aqui fala em voz alta nem abre janela."""
import copy
import json
import os
import time

import pytest


@pytest.fixture
def filtros_temp(monitor, monkeypatch, tmp_path):
    """filtros.json numa pasta temporária; o estado do módulo volta ao fim do teste."""
    caminho = tmp_path / "filtros.json"
    monkeypatch.setattr(monitor, "ARQ_FILTROS", str(caminho))
    monkeypatch.setattr(monitor, "_FILTROS", dict(monitor._FILTROS))
    monkeypatch.setattr(monitor, "_FILTROS_MTIME", 0.0)
    monkeypatch.setattr(monitor, "pregerar_audios_em_segundo_plano", lambda: None)
    return caminho


def _gravar(caminho, dados):
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


def _filtros_antigos():
    """filtros.json como os PCs têm hoje (2.1.x): sem falas_filtros."""
    return copy.deepcopy(FILTROS_ANTIGOS)


FILTROS_ANTIGOS = {
    "termos_gatilho": ["DILATAÇÃO", "AUTO REFRATOR", "MC", "LENTES"],
    "comparacao": "contem",
    "padrao_sem_match": "ignorar",
    "whitelist": ["DILATAÇÃO - CCC", "AUTO REFRATOR (SEM DILATAR)"],
    "blacklist": ["MC", "LENTES DE CONTATO"],
    "alerta_tela": True,
    "alerta_voz": True,
}


# --- fala por filtro ------------------------------------------------------------------
def test_fala_por_filtro_padrao(monitor, filtros_temp):
    _gravar(filtros_temp, _filtros_antigos())
    cfg = monitor.carregar_filtros(forcar=True)
    for senha in ("DILATAÇÃO - CCC", "DILATAÇÃO-FENIL", "DILATAÇÃO - CMC",
                  "DILATAÇÃO COM PILOCARPINA", "AUTO REFRATOR (SEM DILATAR)"):
        assert monitor.frase_alarme(senha, cfg) == "Atenção! Senha de dilatação", senha
        assert monitor.filtro_da_senha(senha, cfg)["chave"] == "dilatacao"
    assert monitor.frase_alarme("LENTES DE CONTATO", cfg) == "Nova Senha"
    assert monitor.frase_alarme("R2", cfg) == "Atenção! Senha R2."          # sem fala própria = lê a senha
    assert monitor.frase_alarme("CONSULTA - COMUM", cfg) == "Atenção! Senha Consulta - Comum."
    assert monitor.frase_alarme("", cfg) == monitor.FRASE_GENERICA


def test_mc_sem_voz_e_cmc_nao_e_mc(monitor, filtros_temp):
    _gravar(filtros_temp, _filtros_antigos())
    cfg = monitor.carregar_filtros(forcar=True)
    falar, filtro = monitor.voz_ligada_para("MC", cfg)
    assert falar is False and filtro["chave"] == "mc"
    assert monitor.voz_ligada_para("DILATAÇÃO - CMC", cfg)[0] is True     # 'CMC' não cai no MC (exata)
    assert monitor.voz_ligada_para("LENTES DE CONTATO", cfg)[0] is True
    assert monitor.voz_ligada_para("SENHA QUALQUER", cfg) == (True, None)


def _disparar(monitor, monkeypatch, ticket, **extra):
    cfg = dict(monitor.carregar_filtros(), alerta_tela=True, alerta_voz=True,
               anti_repeticao_segundos=0)
    cfg.update(extra)
    janelas, vozes, logs = [], [], []
    monkeypatch.setattr(monitor, "carregar_filtros", lambda forcar=False: cfg)
    monkeypatch.setattr(monitor, "na_ui", lambda funcao, *a: janelas.append(funcao))
    monkeypatch.setattr(monitor, "loop_audio_alarme", lambda: vozes.append(ticket))
    monkeypatch.setattr(monitor, "log_msg", logs.append)
    monkeypatch.setattr(monitor, "alarme_ativo", False)
    monkeypatch.setattr(monitor, "_TICKET_ALARME", ticket)
    monitor.disparar_estado_alarme()
    time.sleep(0.2)   # a thread da voz (falsa) só registra o ticket
    return janelas, vozes, logs


def test_disparo_mc_mostra_janela_sem_voz(monitor, filtros_temp, monkeypatch):
    _gravar(filtros_temp, _filtros_antigos())
    monitor.carregar_filtros(forcar=True)
    janelas, vozes, logs = _disparar(monitor, monkeypatch, "MC")
    assert janelas == [monitor.criar_janela_alarme] and vozes == []
    assert any("voz desligada no filtro 'Marcação MC'" in l for l in logs)


def test_disparo_dilatacao_fala(monitor, filtros_temp, monkeypatch):
    _gravar(filtros_temp, _filtros_antigos())
    monitor.carregar_filtros(forcar=True)
    janelas, vozes, _logs = _disparar(monitor, monkeypatch, "DILATAÇÃO - CCC")
    assert vozes == ["DILATAÇÃO - CCC"] and len(janelas) == 1


def test_disparo_mc_sem_tela_nao_faz_nada(monitor, filtros_temp, monkeypatch):
    _gravar(filtros_temp, _filtros_antigos())
    monitor.carregar_filtros(forcar=True)
    janelas, vozes, logs = _disparar(monitor, monkeypatch, "MC", alerta_tela=False)
    assert janelas == [] and vozes == [] and monitor.alarme_ativo is False
    assert any("sem voz (filtro)" in l for l in logs)


# --- migração do filtros.json antigo ------------------------------------------------
def test_migracao_filtros_antigo_ganha_padroes(monitor, filtros_temp):
    _gravar(filtros_temp, _filtros_antigos())
    monitor.carregar_filtros(forcar=True)
    gravado = json.loads(filtros_temp.read_text(encoding="utf-8"))
    falas = gravado["falas_filtros"]
    assert falas["dilatacao"]["fala"] == "Atenção! Senha de dilatação" and falas["dilatacao"]["voz"] is True
    assert falas["lentes"]["fala"] == "Nova Senha"
    assert falas["mc"]["voz"] is False
    # o resto do arquivo ficou como estava
    for chave, valor in FILTROS_ANTIGOS.items():
        assert gravado[chave] == valor
    # segunda leitura: nada muda (não regrava)
    mtime = os.path.getmtime(filtros_temp)
    monitor.carregar_filtros(forcar=True)
    assert os.path.getmtime(filtros_temp) == mtime


def test_migracao_mantem_personalizacao(monitor, filtros_temp):
    dados = _filtros_antigos()
    dados["falas_filtros"] = {
        "dilatacao": {"fala": "Dilatação chegou", "voz": False},   # personalizado, sem termos
        "lentes": {"termos": ["LENTES"], "fala": "", "voz": True},  # fala vazia de propósito
        "minha_fila": {"termos": ["CONSULTA"], "comparacao": "contem"},
        # "mc" e "r2" tirados de propósito: não voltam
    }
    _gravar(filtros_temp, dados)
    cfg = monitor.carregar_filtros(forcar=True)
    gravado = json.loads(filtros_temp.read_text(encoding="utf-8"))["falas_filtros"]
    assert gravado["dilatacao"]["fala"] == "Dilatação chegou" and gravado["dilatacao"]["voz"] is False
    assert gravado["dilatacao"]["termos"] == ["DILATAÇÃO", "DILATACAO", "AUTO REFRATOR"]   # só o que faltava
    assert gravado["lentes"]["fala"] == ""
    assert "mc" not in gravado and "r2" not in gravado
    assert gravado["minha_fila"] == {"termos": ["CONSULTA"], "comparacao": "contem"}
    assert monitor.frase_alarme("DILATAÇÃO - CCC", cfg) == "Dilatação chegou"
    assert monitor.voz_ligada_para("DILATAÇÃO - CCC", cfg)[0] is False
    assert monitor.frase_alarme("LENTES DE CONTATO", cfg) == "Atenção! Senha Lentes DE Contato."
    # filtro novo sem fala/voz = comportamento antigo (lê a senha, com voz)
    assert monitor.frase_alarme("CONSULTA - COMUM", cfg) == "Atenção! Senha Consulta - Comum."
    assert monitor.voz_ligada_para("CONSULTA - COMUM", cfg)[0] is True
    assert monitor.voz_ligada_para("MC", cfg) == (True, None)


# --- compatibilidade -----------------------------------------------------------------
def test_compatibilidade_normalizacao(monitor):
    padrao = monitor._falas_normalizadas(None)
    assert [f["chave"] for f in padrao] == ["dilatacao", "lentes", "mc", "r2"]
    assert monitor._falas_normalizadas("lixo") == padrao
    estranho = monitor._falas_normalizadas({"x": "nao e dict", "y": {"termos": "A, B", "voz": "nao"}})
    assert len(estranho) == 1 and estranho[0]["termos"] == ["A", "B"] and estranho[0]["voz"] is False
    assert estranho[0]["fala"] == ""


def test_migracao_nao_mexe_no_padrao_embutido(monitor):
    dados = {}
    assert monitor.migrar_falas_filtros(dados) is True
    dados["falas_filtros"]["mc"]["voz"] = True
    assert monitor.FILTROS_PADRAO["falas_filtros"]["mc"]["voz"] is False
    assert monitor.migrar_falas_filtros(dados) is False


def test_filtros_json_do_repo_tem_as_falas():
    from conftest import RAIZ
    with open(os.path.join(RAIZ, "filtros.json"), encoding="utf-8") as arquivo:
        falas = json.load(arquivo)["falas_filtros"]
    assert falas["dilatacao"]["fala"] == "Atenção! Senha de dilatação"
    assert falas["lentes"]["fala"] == "Nova Senha"
    assert falas["mc"]["voz"] is False
