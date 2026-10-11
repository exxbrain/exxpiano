"""A small QR Code encoder (standard library only): byte mode, error correction L or M, versions 1 to 10 (up to 271 bytes at level L).

Enough for a Wi-Fi join code (`WIFI:T:WPA;S:name;P:password;;`) and a page address. The algorithm follows ISO/IEC 18004: Reed-Solomon
over GF(256), the eight masks with the standard penalty rules, format and version information. Output: a list of rows of booleans
(True = dark module), or SVG / PNG text and bytes.
"""
import struct
import zlib

ECC_LEVELS = {'L': 1, 'M': 0}                     # the two bits written into the format information
ECC_PER_BLOCK = {'L': (-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18), 'M': (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26)}
NUM_BLOCKS = {'L': (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4), 'M': (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5)}
MAX_VERSION = 10
PAD_BYTES = (0xEC, 0x11)


class QrError(ValueError):
    pass


# ---- sizes
def _raw_modules(ver):
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        n = ver // 7 + 2
        result -= (25 * n - 10) * n - 55
        if ver >= 7:
            result -= 36
    return result


def _data_codewords(ver, ecc):
    return _raw_modules(ver) // 8 - ECC_PER_BLOCK[ecc][ver] * NUM_BLOCKS[ecc][ver]


def _alignment_positions(ver):
    if ver == 1:
        return []
    n = ver // 7 + 2
    size = ver * 4 + 17
    step = (ver * 4 + n * 2 + 1) // (n * 2 - 2) * 2
    return [6] + [size - 7 - i * step for i in range(n - 2, -1, -1)]


# ---- Reed-Solomon over GF(256), polynomial 0x11D
def _gf_mul(x, y):
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data, divisor):
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


# ---- the bit stream
def _make_codewords(data, ver, ecc):
    bits = [0, 1, 0, 0]                                            # byte mode
    count_bits = 8 if ver < 10 else 16
    bits += [(len(data) >> i) & 1 for i in range(count_bits - 1, -1, -1)]
    for byte in data:
        bits += [(byte >> i) & 1 for i in range(7, -1, -1)]
    capacity = _data_codewords(ver, ecc) * 8
    bits += [0] * min(4, capacity - len(bits))                      # terminator
    bits += [0] * (-len(bits) % 8)
    pad = 0
    while len(bits) < capacity:
        bits += [(PAD_BYTES[pad % 2] >> i) & 1 for i in range(7, -1, -1)]
        pad += 1
    words = [int(''.join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]

    blocks_n, ecc_len = NUM_BLOCKS[ecc][ver], ECC_PER_BLOCK[ecc][ver]
    raw_words = _raw_modules(ver) // 8
    short_blocks = blocks_n - raw_words % blocks_n
    short_len = raw_words // blocks_n
    divisor = _rs_divisor(ecc_len)
    blocks, k = [], 0
    for i in range(blocks_n):
        size = short_len - ecc_len + (0 if i < short_blocks else 1)
        chunk = words[k:k + size]
        k += size
        blocks.append((chunk, _rs_remainder(chunk, divisor)))
    out = []
    for i in range(max(len(chunk) for chunk, _ in blocks)):         # data words, block by block (the short blocks have one less)
        for chunk, _ in blocks:
            if i < len(chunk):
                out.append(chunk[i])
    for i in range(ecc_len):                                        # then the error correction words
        for chunk, rem in blocks:
            out.append(rem[i])
    return out


# ---- the symbol
class _Symbol:
    def __init__(self, ver, ecc, codewords):
        self.ver, self.ecc, self.size = ver, ecc, ver * 4 + 17
        self.modules = [[False] * self.size for _ in range(self.size)]
        self.function = [[False] * self.size for _ in range(self.size)]
        self._draw_function_patterns()
        self._draw_codewords(codewords)

    def _set(self, x, y, dark):
        self.modules[y][x] = bool(dark)
        self.function[y][x] = True

    def _draw_function_patterns(self):
        for i in range(self.size):                                  # timing
            self._set(6, i, i % 2 == 0)
            self._set(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (self.size - 4, 3), (3, self.size - 4)):   # finders with their separators
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < self.size and 0 <= y < self.size:
                        self._set(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _alignment_positions(self.ver)
        for i, cy in enumerate(pos):
            for j, cx in enumerate(pos):
                if (i == 0 and j == 0) or (i == 0 and j == len(pos) - 1) or (i == len(pos) - 1 and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self._set(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
        self._draw_format(0)                                        # reserves the areas
        self._draw_version()

    def _draw_format(self, mask):
        data = ECC_LEVELS[self.ecc] << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1
        for i in range(0, 6):
            self._set(8, i, bit(i))
        self._set(8, 7, bit(6))
        self._set(8, 8, bit(7))
        self._set(7, 8, bit(8))
        for i in range(9, 15):
            self._set(14 - i, 8, bit(i))
        for i in range(0, 8):
            self._set(self.size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self._set(8, self.size - 15 + i, bit(i))
        self._set(8, self.size - 8, True)                            # the always-dark module

    def _draw_version(self):
        if self.ver < 7:
            return
        rem = self.ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = self.ver << 12 | rem
        for i in range(18):
            dark = (bits >> i) & 1
            a, b = self.size - 11 + i % 3, i // 3
            self._set(a, b, dark)
            self._set(b, a, dark)

    def _draw_codewords(self, codewords):
        bits = [(w >> i) & 1 for w in codewords for i in range(7, -1, -1)]
        k = 0
        for right in range(self.size - 1, 0, -2):
            if right <= 6:
                right -= 1
            for vert in range(self.size):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = self.size - 1 - vert if upward else vert
                    if not self.function[y][x] and k < len(bits):
                        self.modules[y][x] = bool(bits[k])
                        k += 1

    def _apply_mask(self, mask):
        for y in range(self.size):
            for x in range(self.size):
                invert = ((x + y) % 2 == 0, y % 2 == 0, x % 3 == 0, (x + y) % 3 == 0, (x // 3 + y // 2) % 2 == 0,
                          x * y % 2 + x * y % 3 == 0, (x * y % 2 + x * y % 3) % 2 == 0, ((x + y) % 2 + x * y % 3) % 2 == 0)[mask]
                if invert and not self.function[y][x]:
                    self.modules[y][x] = not self.modules[y][x]

    def _penalty(self):
        n, m, score = self.size, self.modules, 0
        lines = [row for row in m] + [[m[y][x] for y in range(n)] for x in range(n)]
        for line in lines:                                           # runs of the same colour, and finder-like patterns
            run = 1
            for i in range(1, n):
                if line[i] == line[i - 1]:
                    run += 1
                else:
                    score += run - 2 if run >= 5 else 0
                    run = 1
            score += run - 2 if run >= 5 else 0
            text = ''.join('1' if v else '0' for v in line)
            for pat in ('10111010000', '00001011101'):
                score += 40 * sum(1 for i in range(n - 10) if text.startswith(pat, i))
        for y in range(n - 1):                                       # 2x2 blocks
            for x in range(n - 1):
                if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    score += 3
        dark = sum(v for row in m for v in row)
        total = n * n                                                # balance of dark and light: 10 points per 5 % away from half
        score += 10 * ((abs(dark * 20 - total * 10) + total - 1) // total - 1)
        return score


def encode(text, ecc='M', mask=None, version=None):
    """The QR code for `text` as a list of rows of booleans (True = dark). The smallest version that fits is used
    (`version`, `mask` force a choice: for tests)."""
    if ecc not in ECC_LEVELS:
        raise QrError('error correction must be L or M')
    data = text.encode('utf-8') if isinstance(text, str) else bytes(text)
    for ver in range(version or 1, MAX_VERSION + 1):
        need = 4 + (8 if ver < 10 else 16) + 8 * len(data)
        if need <= _data_codewords(ver, ecc) * 8:
            break
    else:
        raise QrError('the text is too long for this encoder (at most %d bytes)' % (_data_codewords(MAX_VERSION, 'L') - 3))
    codewords = _make_codewords(data, ver, ecc)
    best = None
    for m in ([mask] if mask is not None else range(8)):
        trial = _Symbol(ver, ecc, codewords)
        trial._apply_mask(m)
        trial._draw_format(m)
        penalty = trial._penalty()
        if best is None or penalty < best[0]:
            best = (penalty, trial)
    return [row[:] for row in best[1].modules]


# ---- output
def to_svg(matrix, border=4, dark='#000000', light='#ffffff'):
    n = len(matrix)
    size = n + 2 * border
    cells = ''.join('M%d,%dh1v1h-1z' % (x + border, y + border) for y, row in enumerate(matrix) for x, v in enumerate(row) if v)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" shape-rendering="crispEdges">'
            '<rect width="100%%" height="100%%" fill="%s"/><path d="%s" fill="%s"/></svg>') % (size, size, light, cells, dark)


def to_png(matrix, scale=8, border=4):
    """A black and white PNG (no libraries: zlib and struct)."""
    n = len(matrix)
    size = (n + 2 * border) * scale
    rows = []
    for y in range(size):
        my = y // scale - border
        bits = [0 if (0 <= my < n and 0 <= x // scale - border < n and matrix[my][x // scale - border]) else 1 for x in range(size)]
        bits += [1] * (-size % 8)                                    # the row is padded with white to whole bytes
        packed = bytes(int(''.join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8))
        rows.append(b'\x00' + packed)

    def chunk(tag, body):
        return struct.pack('>I', len(body)) + tag + body + struct.pack('>I', zlib.crc32(tag + body) & 0xFFFFFFFF)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 1, 0, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b''.join(rows), 9)) + chunk(b'IEND', b''))


def wifi_text(ssid, password=None, hidden=False):
    """The text phones read as "join this Wi-Fi": WIFI:T:WPA;S:name;P:password;;  (\\ ; , : and " are escaped)."""
    def esc(value):
        return ''.join('\\' + c if c in '\\;,:"' else c for c in value)
    kind = 'WPA' if password else 'nopass'
    return 'WIFI:T:%s;S:%s;%s%s;' % (kind, esc(ssid), 'P:%s;' % esc(password) if password else '', 'H:true;' if hidden else '')
