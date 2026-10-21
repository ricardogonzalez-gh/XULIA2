"""Panel de estado de XULIA2: dónde está el usuario, qué se oyó y qué puede decir.

Quien controla el PC solo por voz necesita saber DÓNDE ESTÁ y QUÉ PUEDE DECIR sin mirar la consola.
Adaptado del panel y de la barra de dicas de la versión 1.5 de XULIA, cuyo diseño nació de pruebas con
usuarios reales (tamaños y contrastes pensados para leerse de un vistazo):

  PANEL (392 x 44 px)  micrófono · lo último que se oyó entre comillas (amarillo; blanco si no se entendió) ·
                       MODO · estado (escuchando / hecho / no entendí) · fichas (AA, SEL, ×3, acento…)
  BARRA DE DICA        tarjeta de 2-3 líneas encima del panel: cómo actuar y cómo salir. Se queda mientras el
                       modo la pide (rejilla, botones, desactivada…) o sale unos segundos al cambiar de modo.

Los dos: no roban el foco, no salen en Alt+Tab y dejan pasar el clic (WS_EX_NOACTIVATE | TOOLWINDOW |
TRANSPARENT), así que nunca estorban lo que se está haciendo. Se consulta el estado del asistente cada
300 ms (no depende de que cada cambio avise). En el dictado nunca se muestra lo dictado.

Se apaga con `[general] panel = no` en %APPDATA%\\Xulia2\\config.ini.
"""
import configparser
import ctypes
import os
import time
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes

# --- colores y medidas de la 1.5 --------------------------------------------------------------
FONDO = "#3F2252"        # tarjeta del panel
LAVANDA = "#B4A2CC"      # borde del panel
AMARILLO = "#EEC833"     # lo que se oyó / alertas
BLANCO = "#FFFFFF"
ESCUCHANDO = "#6BC9A6"   # señal "escuchando"
RECHAZO = "#D27F7B"      # señal "error" / desactivada / no entendí
DICA_FONDO, DICA_BORDE = "#1D212B", "#3A4152"
DICA_T1, DICA_T2 = "#F2F4F8", "#9FB4D4"
CLAVE = "#FF00FF"        # color que la ventana deja transparente (las esquinas redondeadas)

ANCHO, ALTO, PAD, MIC, GAP, CHIP_ALTO, CHIP_GAP, RADIO = 392, 44, 5, 34, 7, 20, 4, 9
PX_HABLA, PX_LINEA2, PX_CHIP = 16, 14, 11   # tamaños en píxeles
MARGEN = 14                # de la esquina inferior izquierda del área de trabajo
DICA_PAD_X, DICA_PAD_Y, DICA_RADIO = 22, 12, 14

PERSISTENT_HINTS = {'grid', 'buttons', 'inactive', 'paused', 'accent'}
HINT_SECONDS = 8
LAST_SECONDS = 4         # cuánto se queda "lo último que se oyó" antes de volver al texto de reposo
POLL_MS = 300

# modo -> (nombre, cómo actuar, cómo salir, reposo)
TEXTS = {
    'es': {
        'command': ("COMANDO", "Di «ayuda» para ver lo que puedes decir", "«modo dictado» para escribir", "di «ayuda»"),
        'dictation': ("DICTADO", "Lo que digas se escribe en la ventana activa",
                      "«salir del modo dictado» para volver a los comandos", "dictando…"),
        'spell': ("DELETREAR", "Di una letra (alicante, bilbao, cadiz…) o «tecla 5»",
                  "«salir del modo deletrear» para volver", "di una letra"),
        'accent': ("ACENTO", "Di la vocal que lleva el acento", "", "di la vocal"),
        'grid': ("REJILLA", "Di «casilla N» (1 a 300) para mover el cursor; «posición 1-9» afina",
                 "«cancelar» para cerrarla", "di «casilla N»"),
        'buttons': ("BOTONES", "Di el número para hacer clic (por ejemplo «doce» o «número doce»)",
                    "los números vuelven para el siguiente · «cancelar» sale", "di el número"),
        'inactive': ("DESACTIVADA", "No ejecuto comandos", "Di «activar» para volver", "di «activar»"),
        'paused': ("EN PAUSA", "Silencio temporal", "Después quedará desactivada: di «activar»", "en pausa"),
        'listening': "escuchando", 'done': "hecho", 'refused': "no entendí", 'dictating': "dictando",
        'inactive_state': "desactivada", 'paused_state': "en pausa",
    },
    'pt': {
        'command': ("COMANDO", "Diga «ajuda» para ver o que pode dizer", "«modo ditado» para escrever", "diga «ajuda»"),
        'dictation': ("DITADO", "O que você disser é escrito na janela ativa",
                      "«sair do ditado» para voltar aos comandos", "ditando…"),
        'spell': ("SOLETRAR", "Diga uma letra (alicante, bilbao, cadiz…) ou «tecla 5»",
                  "«sair do modo soletrar» para voltar", "diga uma letra"),
        'accent': ("ACENTO", "Diga a vogal que leva o acento", "", "diga a vogal"),
        'grid': ("GRADE", "Diga «célula N» (1 a 300) para mover o cursor; «posição 1-9» ajusta",
                 "«cancelar» para fechá-la", "diga «célula N»"),
        'buttons': ("BOTÕES", "Diga o número para clicar (por exemplo «doze» ou «número doze»)",
                    "os números voltam para o próximo · «cancelar» sai", "diga o número"),
        'inactive': ("DESATIVADA", "Não executo comandos", "Diga «ativar» para voltar", "diga «ativar»"),
        'paused': ("EM PAUSA", "Silêncio temporário", "Depois fica desativada: diga «ativar»", "em pausa"),
        'listening': "ouvindo", 'done': "feito", 'refused': "não entendi", 'dictating': "ditando",
        'inactive_state': "desativada", 'paused_state': "em pausa",
    },
}
MODES = ('command', 'dictation', 'spell', 'accent', 'grid', 'buttons', 'inactive', 'paused')

_GWL_EXSTYLE = -20
_WS_EX_LAYERED, _WS_EX_TRANSPARENT, _WS_EX_TOOLWINDOW, _WS_EX_NOACTIVATE = 0x00080000, 0x00000020, 0x00000080, 0x08000000
_GA_ROOT, _HWND_TOPMOST, _SWP_NOSIZE_NOMOVE_NOACTIVATE = 2, -1, 0x0001 | 0x0002 | 0x0010
NO_FOCUS_STYLE = _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE


# --- lógica pura (probada sin ventanas) -------------------------------------------------------

def panel_enabled():
    """True salvo que %APPDATA%\\Xulia2\\config.ini diga `[general] panel = no`."""
    try:
        cfg = configparser.ConfigParser()
        cfg.read(os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'Xulia2', 'config.ini'),
                 encoding='utf-8')
        return cfg.get('general', 'panel', fallback='si').strip().lower() not in ('no', 'off', '0', 'false')
    except Exception:
        return True


def mode_from_state(active, paused, grid, buttons, dictation, spell, accent):
    """Modo que debe mostrar el panel; la prioridad es de arriba abajo."""
    if not active:
        return 'paused' if paused else 'inactive'
    if grid:
        return 'grid'
    if buttons:
        return 'buttons'
    if dictation:
        return 'dictation'
    if spell:
        return 'spell'
    if accent:
        return 'accent'
    return 'command'


def texts_for(language):
    """Textos del idioma ('pt-BR' -> pt); cualquier otro usa español."""
    return TEXTS['pt'] if (language or '').lower().startswith('pt') else TEXTS['es']


def signal_for(mode, verdict):
    """Señal del panel: 'listening' (verde), 'alert' (amarillo) o 'error' (rojo)."""
    if mode == 'inactive' or verdict == 'refused':
        return 'error'
    if mode in ('paused', 'accent'):
        return 'alert'
    return 'listening'


def signal_color(signal):
    return {'listening': ESCUCHANDO, 'alert': AMARILLO, 'error': RECHAZO}[signal]


def fit_text(measure, text, width):
    """Recorta `text` con «…» para que `measure(texto)` no pase de `width` (mejor un recorte que texto cortado)."""
    if not text or width <= 0:
        return ""
    if measure(text) <= width:
        return text
    cut = text
    while len(cut) > 1 and measure(cut + "…") > width:
        cut = cut[:-1]
    return cut.rstrip() + "…"


def main_line(heard, rest, dictating):
    """(texto, entre_comillas) de la línea de arriba: lo oído (amarillo, entre comillas) o el texto de
    reposo (blanco). Dictando nunca se muestra lo dictado: se muestra el reposo."""
    if heard and not dictating:
        return heard, True
    return rest, False


def chips_fit(measure, chips, right, limit):
    """Qué fichas caben entre `limit` y `right` (de derecha a izquierda, sin partir ninguna). Devuelve
    [(texto, relleno, color, x_izq, x_der)] y la x donde termina el espacio ocupado."""
    placed, x = [], right
    for text, filled, color in chips:
        width = max(CHIP_ALTO, measure(text) + 10)
        left = x - width
        if left < limit:
            break   # dejó de caber: se para de golpe
        placed.append((text, filled, color, left, x))
        x = left - CHIP_GAP
    return placed, x


def accent_glyph(kind):
    return {'acento': '´', 'grave': '`', 'circunflejo': '^', 'dieresis': '¨'}.get(kind, '´')


def _apply_no_focus_style(window):
    """Quita el foco, el Alt+Tab y el clic a la ventana. Devuelve el identificador de la ventana."""
    user32 = ctypes.windll.user32
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    hwnd = user32.GetAncestor(window.winfo_id(), _GA_ROOT) or window.winfo_id()
    style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style | NO_FOCUS_STYLE)
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
    return hwnd


def _work_area():
    """(izquierda, arriba, derecha, abajo) del área sin la barra de tareas, en el monitor principal."""
    rect = wintypes.RECT()
    if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
        return rect.left, rect.top, rect.right, rect.bottom
    return 0, 0, ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)


def _family():
    """Montserrat (la fuente de la 1.5) si está instalada, o una de respaldo."""
    installed = {name.lower(): name for name in tkfont.families()}
    for candidate in ("Montserrat", "Poppins", "Segoe UI Semibold", "Segoe UI"):
        if candidate.lower() in installed:
            return installed[candidate.lower()]
    return "Segoe UI"


def _round_rect(canvas, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1, x0, y1,
           x0, y1 - r, x0, y0 + r, x0, y0]
    return canvas.create_polygon(pts, smooth=True, **kw)


class PanelState:
    """Lo que el panel muestra; lo arma XULIA2 (`_panel_state`)."""

    def __init__(self, mode='command', language='es', heard='', heard_time=0.0, verdict=None, chips=None):
        self.mode, self.language, self.heard, self.heard_time, self.verdict = mode, language, heard, heard_time, verdict
        self.chips = chips or []   # [(texto, relleno, color)] por orden de importancia

    def key(self, now):
        fresh = bool(self.heard) and (now - self.heard_time) < LAST_SECONDS
        return (self.mode, texts_for(self.language) is TEXTS['pt'], self.heard if fresh else '',
                self.verdict if fresh else None, tuple(self.chips))


class _Card:
    """Una ventana-tarjeta sin foco ni clic (el panel y la barra de dica)."""

    def __init__(self, root):
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        self.win.configure(background=CLAVE)
        self.win.attributes('-transparentcolor', CLAVE)
        self.canvas = tk.Canvas(self.win, bg=CLAVE, highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.win.update_idletasks()
        self.hwnd = _apply_no_focus_style(self.win)

    def place(self, x, y, width, height):
        self.win.geometry(f"{width}x{height}+{x}+{y}")
        self.canvas.configure(width=width, height=height)
        self.win.deiconify()

    def hide(self):
        self.win.withdraw()

    def raise_on_top(self):
        if self.hwnd and self.win.state() != 'withdrawn':
            ctypes.windll.user32.SetWindowPos(self.hwnd, wintypes.HWND(_HWND_TOPMOST), 0, 0, 0, 0,
                                              _SWP_NOSIZE_NOMOVE_NOACTIVATE)

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass


class StatusPanel:
    """Panel + barra de dica. `start()` se ejecuta en el hilo de Tk (por la cola `_tk_queue`).

    state_getter() -> PanelState
    """

    def __init__(self, root_getter, state_getter):
        self._root_getter = root_getter
        self._state_getter = state_getter
        self.window = None          # el panel
        self.hwnd = None
        self._panel = self._hint = None
        self._shown = None
        self._mode_since = 0.0
        self._last_mode = None
        self._family = "Segoe UI"
        self._fonts = {}

    # --- ciclo de vida ---
    def start(self):
        root = self._root_getter()
        if root is None or self._panel is not None:
            return
        self._family = _family()
        self._panel, self._hint = _Card(root), _Card(root)
        self.window, self.hwnd = self._panel.win, self._panel.hwnd
        self._tick()

    def stop(self):
        for card in (self._panel, self._hint):
            if card is not None:
                card.destroy()
        self._panel = self._hint = self.window = None

    def _font(self, px, bold):
        key = (px, bold)
        if key not in self._fonts:
            self._fonts[key] = tkfont.Font(family=self._family, size=-px, weight='bold' if bold else 'normal')
        return self._fonts[key]

    # --- bucle de consulta ---
    def _tick(self):
        if self._panel is None:
            return
        try:
            state = self._state_getter()
            now = time.time()
            if state.mode != self._last_mode:
                self._last_mode, self._mode_since = state.mode, now
            show_hint = state.mode in PERSISTENT_HINTS or (now - self._mode_since) < HINT_SECONDS
            key = state.key(now) + (show_hint,)
            if key != self._shown:
                self._shown = key
                self._render(state, now, show_hint)
            self._panel.raise_on_top()
            self._hint.raise_on_top()
        except Exception as exc:
            print('Error en el panel de estado:', exc)
        finally:
            if self._panel is not None:
                self._panel.win.after(POLL_MS, self._tick)

    # --- dibujo ---
    def _render(self, state, now, show_hint):
        t = texts_for(state.language)
        name, how, leave, rest = t[state.mode]
        fresh = bool(state.heard) and (now - state.heard_time) < LAST_SECONDS
        heard = state.heard if fresh else ''
        verdict = state.verdict if fresh else None
        signal = signal_for(state.mode, verdict)
        left, top, right, bottom = _work_area()
        x, y = left + MARGEN, bottom - ALTO - 10
        self._draw_panel(t, state, name, rest, heard, verdict, signal)
        self._panel.place(x, y, ANCHO, ALTO)
        lines = [s for s in (how, leave) if s] if show_hint else []
        if lines:
            self._draw_hint(lines, x, y, right - left)
        else:
            self._hint.hide()

    def _draw_panel(self, t, state, name, rest, heard, verdict, signal):
        c = self._panel.canvas
        c.delete('all')
        _round_rect(c, 0, 0, ANCHO - 1, ALTO - 1, RADIO, fill=FONDO,
                    outline=AMARILLO if signal == 'alert' else LAVANDA, width=2)
        # micrófono: círculo, cápsula, arco y pie; tachado si está desactivada
        deactivated = state.mode in ('inactive', 'paused')
        color = RECHAZO if deactivated else signal_color(signal)
        mx, my = PAD, (ALTO - MIC) // 2
        c.create_oval(mx, my, mx + MIC, my + MIC, fill=FONDO, outline=color, width=2)
        cx, cy = mx + MIC / 2, my + MIC / 2
        cap_w, cap_h = MIC * 0.23, MIC * 0.34
        top_y = cy - MIC * 0.29
        _round_rect(c, cx - cap_w / 2, top_y, cx + cap_w / 2, top_y + cap_h, cap_w / 2, fill=color, outline=color)
        ra = MIC * 0.21
        ay = top_y + cap_h - ra * 1.05
        c.create_arc(cx - ra, ay, cx + ra, ay + ra * 1.8, start=200, extent=140, style='arc', outline=color, width=2)
        hy = ay + ra * 1.8 * 0.90
        c.create_line(cx, hy, cx, hy + MIC * 0.11, fill=color, width=2)
        if deactivated:
            c.create_line(mx + 6, my + MIC - 6, mx + MIC - 6, my + 6, fill=RECHAZO, width=3)
        # fichas a la derecha
        chip_font = self._font(PX_CHIP, True)
        placed, x_right = chips_fit(chip_font.measure, state.chips, ANCHO - PAD, PAD + MIC + GAP + 150)
        y0 = (ALTO - CHIP_ALTO) // 2
        for text, filled, chip_color, left, right in placed:
            if filled:
                _round_rect(c, left, y0, right, y0 + CHIP_ALTO, 6, fill=chip_color, outline=chip_color)
                c.create_text((left + right) / 2, y0 + CHIP_ALTO / 2, text=text, fill=FONDO, font=chip_font)
            else:
                _round_rect(c, left, y0, right, y0 + CHIP_ALTO, 6, fill=FONDO, outline=chip_color, width=2)
                c.create_text((left + right) / 2, y0 + CHIP_ALTO / 2, text=text, fill=chip_color, font=chip_font)
        # texto: línea de arriba y línea de modo / estado
        x_text = PAD + MIC + GAP
        width = x_right - x_text - GAP
        if width < 40:
            return   # no sobró espacio: mejor nada que basura cortada
        text, quoted = main_line(heard, rest, state.mode == 'dictation')
        shown = ("“" + text + "”") if quoted else text
        main_color = AMARILLO if (quoted and verdict != 'refused') else BLANCO
        f1 = self._font(PX_HABLA, True)
        c.create_text(x_text, 3, text=fit_text(f1.measure, shown, width), fill=main_color, font=f1, anchor='nw')
        f2b, f2 = self._font(PX_LINEA2, True), self._font(PX_LINEA2, False)
        xx = x_text
        c.create_text(xx, 24, text=name, fill=BLANCO, font=f2b, anchor='nw')
        xx += f2b.measure(name) + 4
        c.create_oval(xx, 31, xx + 3, 34, fill=LAVANDA, outline=LAVANDA)
        xx += 9
        c.create_oval(xx, 29, xx + 7, 36, fill=signal_color(signal), outline=signal_color(signal))
        xx += 11
        if state.mode in ('inactive', 'paused'):
            status = t[state.mode + '_state']   # desactivada: no está escuchando
        elif state.mode == 'dictation':
            status = t['dictating']
        else:
            status = t[verdict] if verdict else t['listening']
        c.create_text(xx, 24, text=fit_text(f2.measure, status, x_text + width - xx), fill=BLANCO, font=f2, anchor='nw')

    def _draw_hint(self, lines, panel_x, panel_y, screen_width):
        card = self._hint
        c = card.canvas
        c.delete('all')
        f1, f2 = self._font(18, True), self._font(15, False)   # 13,5 pt y 11,5 pt de la 1.5, en píxeles
        max_text = max(280, min(int(screen_width / 3) - MARGEN - 2 * DICA_PAD_X,
                                int(screen_width * 0.40) - 2 * DICA_PAD_X))
        fonts = [f1 if i == 0 else f2 for i in range(len(lines))]
        lines = [fit_text(fonts[i].measure, s, max_text) for i, s in enumerate(lines)]
        widths = [fonts[i].measure(s) for i, s in enumerate(lines)]
        heights = [fonts[i].metrics('linespace') for i in range(len(lines))]
        width = max(widths) + 2 * DICA_PAD_X
        height = sum(heights) + 4 * (len(lines) - 1) + 2 * DICA_PAD_Y
        _round_rect(c, 0, 0, width - 1, height - 1, DICA_RADIO, fill=DICA_FONDO, outline=DICA_BORDE)
        y = DICA_PAD_Y
        for i, s in enumerate(lines):
            c.create_text(DICA_PAD_X, y, text=s, fill=DICA_T1 if i == 0 else DICA_T2, font=fonts[i], anchor='nw')
            y += heights[i] + 4
        card.place(panel_x, panel_y - height - 8, width, height)
