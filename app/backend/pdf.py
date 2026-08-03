"""
Generador de PDF minimalista, sin dependencias externas.

El entorno no tiene reportlab ni fpdf y no se puede asumir que se puedan
instalar en el servidor del aeropuerto, así que se escribe el PDF a mano. Es
un subconjunto acotado del formato: suficiente para informes de auditoría
(texto, tablas, líneas, fotos JPEG) y nada más.

Decisiones:
  - Fuentes base (Helvetica) con WinAnsiEncoding: cubre los acentos y la ñ del
    español sin tener que embeber un archivo de fuente.
  - Coordenadas en puntos, origen abajo-izquierda (nativo de PDF), pero la API
    expone `y` desde arriba porque es como se piensa un informe.
  - Las fotos se embeben como XObject DCTDecode, que acepta el JPEG tal cual
    sin recomprimir.
"""

from __future__ import annotations

import zlib

A4 = (595.28, 841.89)          # puntos
MARGEN = 42

# Anchos de Helvetica en unidades/1000, para poder cortar líneas sin desbordar.
_W_REG = {
    ' ': 278, '!': 278, '"': 355, '#': 556, '$': 556, '%': 889, '&': 667,
    "'": 191, '(': 333, ')': 333, '*': 389, '+': 584, ',': 278, '-': 333,
    '.': 278, '/': 278, ':': 278, ';': 278, '<': 584, '=': 584, '>': 584,
    '?': 556, '@': 1015, '[': 278, '\\': 278, ']': 278, '^': 469, '_': 556,
    '`': 333, '{': 334, '|': 260, '}': 334, '~': 584,
    'A': 667, 'B': 667, 'C': 722, 'D': 722, 'E': 667, 'F': 611, 'G': 778,
    'H': 722, 'I': 278, 'J': 500, 'K': 667, 'L': 556, 'M': 833, 'N': 722,
    'O': 778, 'P': 667, 'Q': 778, 'R': 722, 'S': 667, 'T': 611, 'U': 722,
    'V': 667, 'W': 944, 'X': 667, 'Y': 667, 'Z': 611,
    'a': 556, 'b': 556, 'c': 500, 'd': 556, 'e': 556, 'f': 278, 'g': 556,
    'h': 556, 'i': 222, 'j': 222, 'k': 500, 'l': 222, 'm': 833, 'n': 556,
    'o': 556, 'p': 556, 'q': 556, 'r': 333, 's': 500, 't': 278, 'u': 556,
    'v': 500, 'w': 722, 'x': 500, 'y': 500, 'z': 500,
}
_W_BOLD = dict(_W_REG, **{
    'A': 722, 'B': 722, 'C': 722, 'D': 722, 'J': 556, 'K': 722, 'L': 611,
    'a': 556, 'b': 611, 'c': 556, 'd': 611, 'e': 556, 'f': 333, 'g': 611,
    'h': 611, 'k': 556, 'm': 889, 'n': 611, 'o': 611, 'p': 611, 'q': 611,
    'r': 389, 's': 556, 't': 333, 'u': 611, 'v': 556, 'w': 778, 'x': 556,
    'y': 556, 'z': 500, 'i': 278, 'j': 278, ':': 333, ';': 333,
})
for _d in '0123456789':
    _W_REG[_d] = 556
    _W_BOLD[_d] = 556

# Las acentuadas comparten el ancho de su letra base: aproximación suficiente
# para cortar líneas, y evita cargar la tabla AFM completa.
_ANCHO_BASE = {'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u', 'ü': 'u',
               'ñ': 'n', 'Á': 'A', 'É': 'E', 'Í': 'I', 'Ó': 'O', 'Ú': 'U',
               'Ñ': 'N', '°': 'o', '·': '.', '—': '-', '–': '-', '“': '"',
               '”': '"', '‘': "'", '’': "'", '€': 'E', '≥': '=', '≤': '=',
               '→': '-', '✓': 'v', '×': 'x', '½': '1', '¿': '?', '¡': '!'}

# Caracteres que WinAnsi no tiene y hay que escribir de otra forma. Sin esto
# saldrían como '?' en el PDF: en un informe que se firma, un signo perdido en
# medio de una frase es peor que una transliteración explícita.
_SUSTITUTOS = {'→': '->', '←': '<-', '≥': '>=', '≤': '<=', '✓': 'OK',
               '✗': 'X', '−': '-', ' ': ' ', '​': ''}


def _a_winansi(texto: str) -> str:
    """Deja el texto en caracteres que WinAnsi pueda representar.

    Los que existen en cp1252 (acentos, ñ, °, ·) pasan tal cual; los que no
    (flechas, ✓, ≥) se transliteran en vez de perderse como '?'.
    """
    salida = []
    for ch in texto:
        try:
            ch.encode('cp1252')
            salida.append(ch)
        except UnicodeEncodeError:
            salida.append(_SUSTITUTOS.get(ch, _ANCHO_BASE.get(ch, '?')))
    return ''.join(salida)


def ancho_texto(texto: str, tam: float, negrita: bool = False) -> float:
    tabla = _W_BOLD if negrita else _W_REG
    total = 0
    for ch in _a_winansi(str(texto)):
        total += tabla.get(_ANCHO_BASE.get(ch, ch), 500)
    return total * tam / 1000.0


def _escapar(texto: str) -> bytes:
    """Codifica a WinAnsi (cp1252) y escapa los caracteres especiales de PDF."""
    crudo = _a_winansi(str(texto)).encode('cp1252', errors='replace')
    return (crudo.replace(b'\\', b'\\\\')
                 .replace(b'(', b'\\(')
                 .replace(b')', b'\\)'))


class PDF:
    """Documento PDF en construcción.

    El eje `y` que recibe la API se mide desde el borde superior, que es como
    se piensa la maqueta de un informe; internamente se invierte.
    """

    def __init__(self, tamano=A4):
        self.ancho, self.alto = tamano
        self.paginas = []          # lista de listas de operadores
        self.imagenes = []         # (nombre, jpeg_bytes, ancho_px, alto_px)
        self.nueva_pagina()

    # -- páginas -----------------------------------------------------------

    def nueva_pagina(self):
        self._ops = []
        self.paginas.append(self._ops)
        return self

    @property
    def paginas_totales(self):
        return len(self.paginas)

    def _y(self, y):
        return self.alto - y

    # -- dibujo ------------------------------------------------------------

    def texto(self, x, y, texto, tam=10, negrita=False, color=(0, 0, 0)):
        if texto is None or texto == '':
            return self
        fuente = 'F2' if negrita else 'F1'
        r, g, b = color
        self._ops.append(
            b'BT /%s %.2f Tf %.3f %.3f %.3f rg %.2f %.2f Td (%s) Tj ET' % (
                fuente.encode(), tam, r, g, b, x, self._y(y), _escapar(str(texto))))
        return self

    def texto_derecha(self, x_der, y, texto, tam=10, negrita=False, color=(0, 0, 0)):
        """Texto alineado a la derecha: imprescindible para columnas numéricas."""
        ancho = ancho_texto(str(texto), tam, negrita)
        return self.texto(x_der - ancho, y, texto, tam, negrita, color)

    def parrafo(self, x, y, texto, ancho_max, tam=9, negrita=False,
                color=(0, 0, 0), interlinea=1.35):
        """Escribe texto con corte de línea. Devuelve la `y` siguiente."""
        if not texto:
            return y
        palabras = str(texto).split()
        linea, lineas = '', []
        for p in palabras:
            prueba = (linea + ' ' + p).strip()
            if ancho_texto(prueba, tam, negrita) <= ancho_max:
                linea = prueba
            else:
                if linea:
                    lineas.append(linea)
                linea = p
        if linea:
            lineas.append(linea)

        salto = tam * interlinea
        for i, l in enumerate(lineas):
            self.texto(x, y + i * salto, l, tam, negrita, color)
        return y + len(lineas) * salto

    def linea(self, x1, y1, x2, y2, grosor=0.5, color=(0.7, 0.7, 0.7)):
        r, g, b = color
        self._ops.append(
            b'%.3f %.3f %.3f RG %.2f w %.2f %.2f m %.2f %.2f l S' % (
                r, g, b, grosor, x1, self._y(y1), x2, self._y(y2)))
        return self

    def rect(self, x, y, ancho, alto, relleno=None, borde=None, grosor=0.5):
        if relleno:
            r, g, b = relleno
            self._ops.append(b'%.3f %.3f %.3f rg %.2f %.2f %.2f %.2f re f' % (
                r, g, b, x, self._y(y + alto), ancho, alto))
        if borde:
            r, g, b = borde
            self._ops.append(b'%.3f %.3f %.3f RG %.2f w %.2f %.2f %.2f %.2f re S' % (
                r, g, b, grosor, x, self._y(y + alto), ancho, alto))
        return self

    def imagen_jpeg(self, x, y, ancho, alto, jpeg: bytes, dims):
        """Embebe un JPEG sin recomprimir (DCTDecode)."""
        nombre = f'Im{len(self.imagenes)}'
        self.imagenes.append((nombre, jpeg, dims[0], dims[1]))
        self._ops.append(b'q %.2f 0 0 %.2f %.2f %.2f cm /%s Do Q' % (
            ancho, alto, x, self._y(y + alto), nombre.encode()))
        return self

    # -- serialización -----------------------------------------------------

    def generar(self) -> bytes:
        objetos = []          # cuerpo de cada objeto, 1-indexado

        def add(cuerpo: bytes) -> int:
            objetos.append(cuerpo)
            return len(objetos)

        def reservar() -> int:
            """Aparta un número de objeto para llenarlo más tarde.

            Hace falta porque el árbol de páginas es circular: cada página
            apunta a su padre y el padre lista a sus hijas.
            """
            objetos.append(None)
            return len(objetos)

        font_reg = add(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica '
                       b'/Encoding /WinAnsiEncoding >>')
        font_bold = add(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold '
                        b'/Encoding /WinAnsiEncoding >>')

        img_refs = {}
        for nombre, jpeg, w, h in self.imagenes:
            img_refs[nombre] = add(
                b'<< /Type /XObject /Subtype /Image /Width %d /Height %d '
                b'/ColorSpace /DeviceRGB /BitsPerComponent 8 '
                b'/Filter /DCTDecode /Length %d >>\nstream\n' % (w, h, len(jpeg))
                + jpeg + b'\nendstream')

        xobjects = b' '.join(b'/%s %d 0 R' % (n.encode(), r)
                             for n, r in img_refs.items())
        recursos = (b'<< /Font << /F1 %d 0 R /F2 %d 0 R >>' % (font_reg, font_bold)
                    + (b' /XObject << ' + xobjects + b' >>' if img_refs else b'')
                    + b' >>')

        id_pages = reservar()
        ids_paginas = []
        for ops in self.paginas:
            contenido = zlib.compress(b'\n'.join(ops))
            id_cont = add(b'<< /Length %d /Filter /FlateDecode >>\nstream\n' %
                          len(contenido) + contenido + b'\nendstream')
            ids_paginas.append(add(
                b'<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] '
                b'/Resources %s /Contents %d 0 R >>' % (
                    id_pages, self.ancho, self.alto, recursos, id_cont)))

        kids = b' '.join(b'%d 0 R' % i for i in ids_paginas)
        objetos[id_pages - 1] = (
            b'<< /Type /Pages /Count %d /Kids [%s] >>' % (len(ids_paginas), kids))
        id_catalogo = add(b'<< /Type /Catalog /Pages %d 0 R >>' % id_pages)

        # -- ensamblado: cuerpo, tabla de referencias cruzadas y trailer --
        salida = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
        posiciones = []
        for i, cuerpo in enumerate(objetos, start=1):
            posiciones.append(len(salida))
            salida += b'%d 0 obj\n' % i + cuerpo + b'\nendobj\n'

        inicio_xref = len(salida)
        salida += b'xref\n0 %d\n' % (len(objetos) + 1)
        salida += b'0000000000 65535 f \n'
        for pos in posiciones:
            salida += b'%010d 00000 n \n' % pos
        salida += (b'trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n'
                   % (len(objetos) + 1, id_catalogo, inicio_xref))
        return bytes(salida)
