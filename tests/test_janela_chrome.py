"""Janela do Chrome (2.2.1): visível = maximizada; escondida = tamanho da tela real
(mínimo 1280x720). Tudo com user32/root/driver falsos: nenhuma janela abre."""
import pytest


class User32Falso:
    def __init__(self, largura=1920, altura=1080, dpi=96, zoomed=False):
        self.largura, self.altura, self.dpi, self.zoomed = largura, altura, dpi, zoomed
        self.chamadas = []

    def GetSystemMetrics(self, indice):
        return {0: self.largura, 1: self.altura}[indice]

    def GetDpiForSystem(self):
        return self.dpi

    def IsZoomed(self, hwnd):
        return self.zoomed

    def ShowWindow(self, hwnd, cmd):
        self.chamadas.append(("ShowWindow", hwnd, cmd))
        if cmd == 3:
            self.zoomed = True

    def SetWindowPos(self, hwnd, depois, x, y, l, a, flags):
        self.chamadas.append(("SetWindowPos", hwnd, x, y, l, a, flags))


class RootFalso:
    def __init__(self):
        self.estado = "withdrawn"

    def withdraw(self):
        self.estado = "withdrawn"

    def deiconify(self):
        self.estado = "normal"

    def state(self, novo=None):
        if novo:
            self.estado = novo
        return self.estado

    def lift(self):
        pass

    def focus_force(self):
        pass

    def winfo_exists(self):
        return True


@pytest.fixture
def ambiente(monitor, monkeypatch):
    def montar(**kw):
        u = User32Falso(**kw)
        monkeypatch.setattr(monitor, "_user32", lambda: u)
        monkeypatch.setattr(monitor, "root", RootFalso())
        monkeypatch.setattr(monitor, "_hwnd_chrome", lambda: 4242)
        monkeypatch.setattr(monitor, "log_msg", lambda *a, **k: None)
        return u
    return montar


@pytest.mark.parametrize("l, a, dpi, esperado", [
    (1920, 1080, 96, (1920, 1080)),
    (1280, 720, 96, (1280, 720)),
    (2560, 1440, 96, (2560, 1440)),
    (1920, 1080, 120, (1536, 864)),     # 125%: o --window-size é em px CSS
    (1024, 768, 96, (1280, 768)),       # abaixo do mínimo: sobe para 1280
    (1280, 720, 120, (1280, 720)),      # 720p a 125% = 1024x576 -> mínimo
    (0, 0, 96, (1280, 720)),            # não leu a tela: mínimo seguro
])
def test_tamanho_escondido_e_a_resolucao_com_minimo(monitor, ambiente, l, a, dpi, esperado):
    ambiente(largura=l, altura=a, dpi=dpi)
    assert monitor.tamanho_janela_chrome() == esperado


def test_argumentos_escondido_e_visivel(monitor):
    escondido = monitor.argumentos_janela_chrome(False, (1920, 1080))
    assert escondido == ["--window-size=1920,1080"]
    visivel = monitor.argumentos_janela_chrome(True, (1280, 720))
    assert "--start-maximized" in visivel and "--window-size=1280,720" in visivel
    # o limbo continua nos argumentos-base (modo escondido de sempre)
    assert "--window-position=-10000,-10000" in monitor.ARGUMENTOS_CHROME


def test_mostrar_maximiza(monitor, ambiente):
    u = ambiente()
    monitor.gerenciar_janelas("mostrar")
    assert ("ShowWindow", 4242, 3) in u.chamadas
    # nada de 1024x768 fixo como antes
    assert not any(c[0] == "SetWindowPos" and c[4:6] == (1024, 768) for c in u.chamadas)
    # saiu do limbo para a tela principal antes de maximizar
    mover = [c for c in u.chamadas if c[0] == "SetWindowPos"]
    assert mover and mover[0][2:4] == (0, 0)


def test_mostrar_ja_maximizado_nao_move(monitor, ambiente):
    u = ambiente(zoomed=True)
    monitor.gerenciar_janelas("mostrar")
    assert u.chamadas == [("ShowWindow", 4242, 3)]


def test_esconder_volta_ao_limbo_com_tamanho_da_tela(monitor, ambiente):
    u = ambiente(largura=1280, altura=720)
    monitor.gerenciar_janelas("ocultar")
    assert ("ShowWindow", 4242, 0) in u.chamadas
    pos = [c for c in u.chamadas if c[0] == "SetWindowPos"][-1]
    assert pos[2:6] == (-10000, -10000, 1280, 720)


def test_esconder_com_escala_usa_pixels_fisicos(monitor, ambiente):
    u = ambiente(largura=1920, altura=1080, dpi=120)
    monitor.gerenciar_janelas("ocultar")
    pos = [c for c in u.chamadas if c[0] == "SetWindowPos"][-1]
    assert pos[4:6] == (1920, 1080)   # 1536x864 CSS * 1,25


def test_esconder_maximizado_so_esconde(monitor, ambiente):
    u = ambiente(zoomed=True)
    monitor.gerenciar_janelas("ocultar")
    assert u.chamadas == [("ShowWindow", 4242, 0)]


def test_abertura_visivel_maximiza_e_reserva_pelo_driver(monitor, ambiente, monkeypatch):
    u = ambiente()
    assert monitor._maximizar_chrome_aberto(4242) is True
    assert ("ShowWindow", 4242, 3) in u.chamadas

    class DriverFalso:
        maximizou = False

        def maximize_window(self):
            DriverFalso.maximizou = True
    monkeypatch.setattr(monitor, "driver", DriverFalso())
    assert monitor._maximizar_chrome_aberto(None) is True
    assert DriverFalso.maximizou
