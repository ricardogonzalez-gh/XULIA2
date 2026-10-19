"""Modo botones numerados: enumeración de los elementos clicables de la ventana activa.

Adaptado de la versión 1.5 de XULIA (BotonesNumerados.cs). Usa UI Automation por COM (comtypes):
la UI Automation "gestionada" antigua no ve dentro de WinUI 3 / WebView2 (WhatsApp, Configuración).

Robustez (quien controla el PC solo por voz no puede quedarse con el programa colgado):
  - búsqueda en ANCHURA con poda (un nodo fuera del área de la ventana no se visita) y presupuesto
    de tiempo y de nodos: las aplicaciones web enormes no estiran el comando;
  - todo corre en un hilo de fondo con tiempo máximo;
  - nunca se inspecciona el propio proceso (un cliente UIA sobre uno mismo es un bloqueo mortal).

Numeración CONTENIDO PRIMERO: en un navegador la búsqueda en anchura halla el "cromo" (barra,
pestañas, favoritos) antes que la página; los elementos dentro de un Document se numeran primero.
"""
import collections
import ctypes
import os
import time
from ctypes import wintypes

import buttons_rules as br
from buttons_rules import Rect

BFS_BUDGET = 3.0          # s: presupuesto interno de la búsqueda
MAX_NODES = 5000          # tope de nodos recorridos (apps web enormes)
ENUM_TIMEOUT = 4.0        # s: tope duro del hilo de enumeración
BAND_TOP_PX = 46          # franja superior donde vive el marco de la ventana

# propiedades UIA que se piden en la caché (una sola llamada entre procesos por elemento)
_P_RECT, _P_PID, _P_TYPE, _P_NAME, _P_FOCUSABLE, _P_ENABLED = 30001, 30002, 30003, 30005, 30009, 30010
_P_OFFSCREEN, _P_ARIA, _P_VALUE, _P_READONLY, _P_INVOKABLE, _P_ISDIALOG = 30022, 30101, 30045, 30046, 30031, 30174
LAYER_ROLES = {"dialog", "alertdialog", "menu"}

user32 = ctypes.windll.user32
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_ENUM_PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [_ENUM_PROC, wintypes.LPARAM]
user32.EnumChildWindows.argtypes = [wintypes.HWND, _ENUM_PROC, wintypes.LPARAM]


# --- ventanas ----------------------------------------------------------------------------------

def class_name(hwnd):
    buf = ctypes.create_unicode_buffer(200)
    user32.GetClassNameW(hwnd, buf, 200)
    return buf.value


def window_title(hwnd):
    buf = ctypes.create_unicode_buffer(300)
    user32.GetWindowTextW(hwnd, buf, 300)
    return buf.value


def window_pid(hwnd):
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def window_rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return Rect.from_ltrb(r.left, r.top, r.right, r.bottom)


def render_windows(window):
    """Ventanas de contenido web (Chrome_RenderWidgetHostHWND) que pertenecen a `window` pero la
    ventana de arriba NO alcanza: contenido alojado en OTRA ventana superior de otro proceso
    (WebView2 es multiproceso; en WhatsApp la ventana de render tiene otro PID). Si la render es de
    la propia ventana (Chrome/Edge normales) la búsqueda normal ya pasa por ella."""
    found = []
    own_title = window_title(window).strip().lower()

    def on_child(kid, _):
        if class_name(kid) != "Chrome_RenderWidgetHostHWND":
            return True
        root = user32.GetAncestor(kid, 2)   # GA_ROOT
        if root == window:
            return True
        root_title = window_title(root).strip().lower()
        if len(own_title) >= 3 and len(root_title) >= 3 and (own_title in root_title or root_title in own_title):
            found.append(kid)
        return True

    child_cb = _ENUM_PROC(on_child)

    def on_top(top, _):
        user32.EnumChildWindows(top, child_cb, 0)
        return True

    try:
        user32.EnumWindows(_ENUM_PROC(on_top), 0)
    except Exception:
        pass
    return found


def behind_taskbar(x, y):
    """El centro está debajo de la barra de tareas (pregunta barata de Windows, sin UIA). Se mira la
    RAÍZ de la ventana en el punto, no el área útil: en pantalla completa la barra no está."""
    try:
        h = user32.WindowFromPoint(wintypes.POINT(x, y))
        if not h:
            return False
        return br.is_taskbar_class(class_name(user32.GetAncestor(h, 2) or h))
    except Exception:
        return False   # ante la duda, el número se queda


# --- UI Automation -----------------------------------------------------------------------------

def load_uia():
    """(módulo UIAutomationClient, IUIAutomation) para el hilo actual; COM multihilo."""
    import comtypes
    import comtypes.client
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except OSError:
        pass   # el hilo ya tenía COM iniciado
    comtypes.client.GetModule("UIAutomationCore.dll")
    from comtypes.gen import UIAutomationClient as uc
    return uc, comtypes.client.CreateObject(uc.CUIAutomation8, interface=uc.IUIAutomation)


def _make_cache(uia):
    cache = uia.CreateCacheRequest()
    for prop in (_P_RECT, _P_TYPE, _P_NAME, _P_FOCUSABLE, _P_ENABLED, _P_OFFSCREEN, _P_ARIA, _P_PID,
                 _P_READONLY, _P_VALUE, _P_INVOKABLE):
        cache.AddProperty(prop)
    try:
        cache.AddProperty(_P_ISDIALOG)   # Windows 10 1809+
    except Exception:
        pass
    return cache


def _cached(el, prop):
    try:
        return el.GetCachedPropertyValue(prop)
    except Exception:
        return None


def _cached_text(el, prop):
    value = _cached(el, prop)
    return value if isinstance(value, str) else ""


def _cached_bool(el, prop):
    value = _cached(el, prop)
    return value if isinstance(value, bool) else False


def _cached_rect(el):
    r = el.CachedBoundingRectangle
    return Rect.from_ltrb(r.left, r.top, r.right, r.bottom)


def _is_layer(el, control_type, in_doc):
    """Diálogo o menú dentro del contenido web (Chrome da role=dialog como Window)."""
    if not in_doc or control_type == br.DOCUMENT:
        return False
    if control_type in (br.MENU, br.WINDOW):
        return True
    if _cached_text(el, _P_ARIA) in LAYER_ROLES:
        return True
    return _cached_bool(el, _P_ISDIALOG)


class _Node:
    """Nodo de la búsqueda en anchura, con la región en la que está."""
    __slots__ = ("el", "in_tab", "in_doc", "layer", "in_numbered", "via_render", "numbered_type",
                 "numbered_serial", "numbered_center", "numbered_rect", "overlay", "document")

    def __init__(self, el, in_tab=False, in_doc=False, layer=-1, in_numbered=False, via_render=False):
        self.el, self.in_tab, self.in_doc, self.layer = el, in_tab, in_doc, layer
        self.in_numbered, self.via_render = in_numbered, via_render
        self.numbered_type, self.numbered_serial = 0, -1
        self.numbered_center, self.numbered_rect = None, Rect()
        self.overlay, self.document = -1, -1


class Target:
    """Un elemento numerable y lo que las fases siguientes necesitan saber de él."""
    __slots__ = ("element", "point", "rect", "ctype", "is_content", "layer", "overlay", "doc", "serial", "url")

    def __init__(self, element, point, rect, ctype, is_content, layer, overlay, doc, serial, url):
        self.element, self.point, self.rect, self.ctype = element, point, rect, ctype
        self.is_content, self.layer, self.overlay, self.doc, self.serial, self.url = is_content, layer, overlay, doc, serial, url


class Enumeration:
    """Resultado de enumerate_targets: los objetivos en el orden del número, más los datos de capas y
    superposiciones que usa la detección de fantasmas."""

    def __init__(self):
        self.targets = []
        self.layer_rects, self.layer_parent = [], []
        self.overlay_rects, self.overlay_parent, self.overlay_doc, self.overlay_elements = [], [], [], []
        self.parent_of_serial = []
        self.hosted = False        # la app aloja su contenido en una ventana de render propia
        self.visited = 0
        self.elapsed = 0.0
        self.note = ""


def enumerate_targets(hwnd, area, show_close_tab=False):
    """Recorre la ventana `hwnd` (y sus ventanas de render) y devuelve una Enumeration. Corre en un
    hilo de fondo; ver collect_targets para llamarla con tope de tiempo."""
    uc, uia = load_uia()
    cache = _make_cache(uia)
    walker = uia.RawViewWalker
    out = Enumeration()

    layer_rects, layer_parent = out.layer_rects, out.layer_parent
    overlay_rects, overlay_parent, overlay_doc = out.overlay_rects, out.overlay_parent, out.overlay_doc
    overlay_elements, parent_of_serial = out.overlay_elements, out.parent_of_serial
    content, chrome = [], []              # Target de la página y del marco, en orden de hallazgo
    with_numbered_item = set()
    taken_points, taken_names = [], []
    next_serial = documents = visited = 0

    root = uia.ElementFromHandleBuildCache(hwnd, cache)
    renders = render_windows(hwnd)
    out.hosted = bool(renders)
    extra_roots = []
    for hr in renders:
        try:
            el = uia.ElementFromHandleBuildCache(hr, cache)
            if el:
                extra_roots.append(el)
        except Exception:
            pass
    is_chromium = class_name(hwnd) == "Chrome_WidgetWin_1"
    render_pids = {window_pid(hr) for hr in renders if window_pid(hr)}

    queue = collections.deque([_Node(root)])
    # contenido web alojado: entra como CONTENIDO (delante en la numeración)
    for el in extra_roots:
        queue.append(_Node(el, in_doc=True, via_render=True))

    started = time.time()
    limit_reached = lambda: (time.time() - started >= BFS_BUDGET or visited >= MAX_NODES
                             or (len(content) >= br.MAX_ELEMENTS and len(chrome) >= br.MAX_ELEMENTS))

    while queue and not limit_reached():
        node = queue.popleft()
        try:
            child = walker.GetFirstChildElementBuildCache(node.el, cache)
        except Exception:
            continue
        try:
            parent_rect = _cached_rect(node.el)
        except Exception:
            parent_rect = Rect()
        parent_type, parent_role, parent_focusable = 0, "", False
        if node.via_render:   # el padre, para la conversación sin papel
            try:
                parent_type = node.el.CachedControlType
                parent_focusable = bool(node.el.CachedIsKeyboardFocusable)
            except Exception:
                pass
            parent_role = _cached_text(node.el, _P_ARIA)

        while child and not limit_reached():
            visited += 1
            try:
                ctype = child.CachedControlType
                repeated_content = False
                if not node.via_render and render_pids:
                    try:
                        repeated_content = child.CachedProcessId in render_pids
                    except Exception:
                        pass
                # se poda (ni se numera ni se baja): barra de título, menú de sistema, barra de
                # desplazamiento (tienen comando propio) y el contenido de render que llegó por arriba
                if repeated_content or ctype in (br.TITLEBAR, br.MENUBAR, br.SCROLLBAR):
                    child = _next(walker, child, cache)
                    continue

                rect = _cached_rect(child)
                rect_valid = rect.width > 1 and rect.height > 1
                layer_here = node.layer
                if rect_valid and _is_layer(child, ctype, node.in_doc):
                    layer_here = len(layer_rects)
                    layer_rects.append(rect)
                    layer_parent.append(node.layer)
                doc_here = documents if ctype == br.DOCUMENT else node.document
                if ctype == br.DOCUMENT:
                    documents += 1
                overlay_here = node.overlay
                if (node.in_doc and rect_valid and ctype != br.DOCUMENT and layer_here == node.layer
                        and br.is_overlay_candidate(rect, parent_rect)):
                    overlay_here = len(overlay_rects)
                    overlay_rects.append(rect)
                    overlay_parent.append(node.overlay)
                    overlay_doc.append(doc_here)
                    overlay_elements.append(child)

                # poda: subárbol totalmente fuera de la ventana; rect vacío = contenedor, sigue bajando
                if not rect_valid or rect.intersects(area):
                    cx, cy = rect.x + rect.width // 2, rect.y + rect.height // 2
                    in_top_band = cy < area.y + BAND_TOP_PX
                    focusable = bool(child.CachedIsKeyboardFocusable)
                    name = child.CachedName or ""
                    name_empty = name.strip() == ""

                    # (1) en el marco de un navegador solo se numera lo ENFOCABLE: quita de golpe los
                    #     "botones fantasma" (favoritos de una barra oculta, restos de pestañas)
                    chrome_not_focusable = (is_chromium and not node.in_doc and not focusable
                                            and not (node.in_tab and show_close_tab))
                    # (2) botones del marco de la ventana: tienen comando de voz propio
                    frame_button = not node.in_doc and in_top_band and br.is_caption_name(name)
                    # (3) sin nombre y (no enfocable o grande): contenedor o icono invisible
                    chat_no_role = node.via_render and node.in_doc and br.chat_without_role(
                        ctype, _cached_bool(child, _P_INVOKABLE), rect, parent_type, parent_role,
                        parent_focusable, parent_rect, node.in_numbered)
                    no_name = name_empty and (not focusable or rect.width >= 220 or rect.height >= 140) and not chat_no_role
                    # (4) imagen dentro de algo ya numerado; (5) emoji suelto en el texto
                    image_inside = br.image_inside_numbered(ctype, node.in_numbered)
                    emoji_only = br.is_emoji_only_image(ctype, name)
                    # (6) pieza de una fila ya numerada; (7) botón que ocupa la fila entera
                    read_only = ctype == br.EDIT and _cached_bool(child, _P_READONLY)
                    row_piece = (br.cell_inside_row(ctype, node.numbered_type, read_only)
                                 or (node.in_doc and br.link_repeats_row_click(
                                     ctype, node.numbered_type, rect, node.numbered_center)))
                    fills_row = node.in_doc and br.button_fills_row(ctype, node.numbered_type, rect, node.numbered_rect)

                    exclude = (chrome_not_focusable or frame_button or no_name or image_inside
                               or emoji_only or row_piece or fills_row)
                    numbered_here, serial_here = False, -1
                    if (rect_valid and not exclude and (br.is_interactive(ctype, focusable) or chat_no_role)
                            and child.CachedIsEnabled and not child.CachedIsOffscreen
                            and area.contains_point(cx, cy) and not behind_taskbar(cx, cy)):
                        point = (cx, cy)
                        if not br.is_duplicate(taken_points, taken_names, point, name):
                            taken_points.append(point)
                            taken_names.append(name)
                            numbered_here, serial_here = True, next_serial
                            next_serial += 1
                            parent_of_serial.append(node.numbered_serial)
                            if node.numbered_serial >= 0:
                                with_numbered_item.add(node.numbered_serial)
                            url = _cached_text(child, _P_VALUE) if ctype == br.HYPERLINK else ""
                            if node.in_doc and len(content) < br.MAX_ELEMENTS:
                                content.append(Target(child, point, rect, ctype, True, layer_here, overlay_here, doc_here, serial_here, url))
                            elif not node.in_doc and len(chrome) < br.MAX_ELEMENTS:
                                chrome.append(Target(child, point, rect, ctype, False, layer_here, overlay_here, doc_here, serial_here, url))

                    nxt = _Node(child, node.in_tab or ctype == br.TABITEM, node.in_doc or ctype == br.DOCUMENT,
                                layer_here, node.in_numbered or numbered_here, node.via_render)
                    nxt.numbered_type = ctype if numbered_here else node.numbered_type
                    nxt.numbered_serial = serial_here if numbered_here else node.numbered_serial
                    nxt.numbered_center = (rect.x + rect.width // 2, rect.y + rect.height // 2) if numbered_here else node.numbered_center
                    nxt.numbered_rect = rect if numbered_here else node.numbered_rect
                    nxt.overlay, nxt.document = overlay_here, doc_here
                    queue.append(nxt)
            except Exception:
                pass   # el elemento desapareció a mitad de camino
            child = _next(walker, child, cache)

    # salida CONTENIDO-PRIMERO; el marco (barra de herramientas, menú) con elementos numerados dentro
    # se queda fuera (regla B)
    for group in (content, chrome):
        for target in group:
            if len(out.targets) >= br.MAX_ELEMENTS:
                break
            if br.frame_with_numbered_items(target.ctype, target.serial in with_numbered_item):
                continue
            out.targets.append(target)
    out.visited, out.elapsed = visited, time.time() - started
    return out


def _next(walker, child, cache):
    try:
        nxt = walker.GetNextSiblingElementBuildCache(child, cache)
        return nxt if nxt else None
    except Exception:
        return None


def collect_targets(hwnd, show_close_tab=False):
    """Enumera la ventana `hwnd` con tope de tiempo. Devuelve (Enumeration o None, motivo)."""
    import threading
    if not hwnd:
        return None, "sin ventana en primer plano"
    if window_pid(hwnd) == os.getpid():
        return None, "la ventana activa es el propio XULIA2"   # cliente UIA sobre uno mismo = bloqueo
    area = window_rect(hwnd)
    box = {}

    def work():
        try:
            box["result"] = enumerate_targets(hwnd, area, show_close_tab)
        except Exception as exc:
            box["error"] = f"{type(exc).__name__}: {exc}"

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(ENUM_TIMEOUT)
    if worker.is_alive():
        return None, f"tiempo agotado en la enumeración UIA ({ENUM_TIMEOUT:.0f} s)"
    if "error" in box:
        return None, "error en la enumeración: " + box["error"]
    result = box["result"]
    if not result.targets:
        return None, "ningún elemento clicable encontrado"
    return result, ""


if __name__ == "__main__":   # sonda: python numbered_buttons.py [hwnd]   (sin argumento, la ventana activa)
    import sys
    handle = int(sys.argv[1], 0) if len(sys.argv) > 1 else user32.GetForegroundWindow()
    result, why = collect_targets(handle)
    if result is None:
        print("sin resultado:", why)
    else:
        print(f"{len(result.targets)} numerados, {result.visited} nodos, {result.elapsed:.2f} s, "
              f"alojado={result.hosted}, capas={len(result.layer_rects)}, superposiciones={len(result.overlay_rects)}")
        for number, t in enumerate(result.targets, 1):
            print(f"{number:3}  tipo={t.ctype}  {'web' if t.is_content else 'app'}  {t.rect}  capa={t.layer}")
