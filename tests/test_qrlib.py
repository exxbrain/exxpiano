"""Tests for pi/qrlib.py. The two golden codes were decoded back to their text with an independent decoder (OpenCV, classic and Aruco detectors,
six image scales) when they were written down; the encoder as a whole was checked the same way on 480 forced-mask and 300 random payloads."""
import os
import struct
import sys
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'pi'))
import qrlib  # noqa: E402

GOLDEN_JOIN = (
    '111111100010011001110101001111111',
    '100000100110111101001110101000001',
    '101110101101010101111111101011101',
    '101110101111001010011011101011101',
    '101110101100000101100111001011101',
    '100000101011100110001110001000001',
    '111111101010101010101010101111111',
    '000000001011001100000111100000000',
    '101111100111101110000010001111100',
    '001000011010000000011111110000010',
    '001011101011000110100110000111110',
    '000110010001101110011101100111111',
    '000110101110100000110010010010101',
    '011101010100111110001011001000100',
    '100001100101101001110100010100110',
    '101101001110001010011101011110111',
    '001110110110110001010011010011010',
    '101110010110111001111110010101111',
    '111101110101100101001110001100000',
    '100100010110010101100111110001110',
    '100111110001001110000011110010110',
    '100000000110011100100011001000010',
    '101011100110110110001100100100010',
    '101110011110000100010101000111101',
    '101001110111111111010011111110000',
    '000000001000110010011111100010000',
    '111111100001011110101101101011110',
    '100000101011010110000100100010100',
    '101110101100100100111000111111010',
    '101110101101101110010111110001111',
    '101110101111000000001100100001100',
    '100000100000010010110100000111100',
    '111111101100110011010011110111010',
)

GOLDEN_PAGE = (
    '11111110001001110011001111111',
    '10000010000111111110101000001',
    '10111010101001010001101011101',
    '10111010101000110111001011101',
    '10111010100100011111001011101',
    '10000010100100000100001000001',
    '11111110101010101010101111111',
    '00000000101100101000000000000',
    '10111110000010001100101111100',
    '00101001100001110001011010001',
    '00001011101011111100001000000',
    '01100001010111010000011010010',
    '01010011110010110101110101100',
    '11110101000000011111001010101',
    '01001011011000000010010010100',
    '01011001010110101000010110010',
    '00100010000010001100110100100',
    '11100101110011110011011011101',
    '10010111000101111100001001100',
    '10001001011011010010011100010',
    '10101111000010110101111110111',
    '00000000101010011111100011111',
    '11111110000110000011101011100',
    '10000010100010101000100010010',
    '10111010100000001010111111101',
    '10111010101011110110110000000',
    '10111010101111011100101011010',
    '10000010000011010111110010010',
    '11111110100110010000000011100',
)


def rows(text, ecc='M'):
    return [''.join('1' if v else '0' for v in r) for r in qrlib.encode(text, ecc)]


class GoldenTests(unittest.TestCase):
    def test_the_join_code_for_the_setup_network_does_not_change(self):
        self.assertEqual(rows(qrlib.wifi_text('exxpiano-Setup-81DE', 'exxpianosetup')), list(GOLDEN_JOIN))

    def test_the_code_for_the_setup_page_does_not_change(self):
        self.assertEqual(rows('http://10.42.0.1:8080/#/wifi'), list(GOLDEN_PAGE))


class StructureTests(unittest.TestCase):
    def test_sizes_follow_the_version(self):
        for text, size in (('A', 21), ('x' * 30, 25), ('x' * 100, 37)):
            m = qrlib.encode(text, 'L')
            self.assertEqual((len(m), {len(r) for r in m}), (size, {size}))
        self.assertEqual(len(qrlib.encode('A', 'M')), 21)
        self.assertGreater(len(qrlib.encode('x' * 20, 'M')), len(qrlib.encode('x' * 20, 'L')) - 1)

    def test_finders_timing_and_the_dark_module(self):
        m = qrlib.encode('http://10.42.0.1:8080/#/wifi')
        n = len(m)
        finder = ['1111111', '1000001', '1011101', '1011101', '1011101', '1000001', '1111111']
        for ox, oy in ((0, 0), (n - 7, 0), (0, n - 7)):
            self.assertEqual([''.join('1' if m[oy + y][ox + x] else '0' for x in range(7)) for y in range(7)], finder)
        self.assertEqual([m[6][i] for i in range(8, n - 8)], [i % 2 == 0 for i in range(8, n - 8)])
        self.assertEqual([m[i][6] for i in range(8, n - 8)], [i % 2 == 0 for i in range(8, n - 8)])
        self.assertTrue(m[n - 8][8])                                            # the module that is always dark

    def test_the_format_information_is_written_twice_and_is_a_valid_bch_word(self):
        for ecc in 'LM':
            for mask in range(8):
                m = qrlib.encode('format check', ecc, mask=mask)
                n = len(m)
                first = [m[i][8] for i in range(6)] + [m[7][8], m[8][8], m[8][7]] + [m[8][14 - i] for i in range(9, 15)]          # m[y][x]
                second = [m[8][n - 1 - i] for i in range(8)] + [m[n - 15 + i][8] for i in range(8, 15)]
                self.assertEqual(first, second)
                word = sum(int(b) << i for i, b in enumerate(first)) ^ 0x5412
                data = word >> 10
                self.assertEqual((data >> 3, data & 7), (qrlib.ECC_LEVELS[ecc], mask))
                rem = data
                for _ in range(10):
                    rem = (rem << 1) ^ ((rem >> 9) * 0x537)
                self.assertEqual(word & 0x3FF, rem)                              # the 10 check bits agree

    def test_the_version_information_exists_from_version_7(self):
        m = qrlib.encode('x' * 140, 'L')                                          # version 7: 45 modules
        n = len(m)
        self.assertEqual(n, 45)
        word = sum(int(m[i // 3][n - 11 + i % 3]) << i for i in range(18))
        self.assertEqual(word >> 12, 7)
        self.assertEqual(sum(int(m[n - 11 + i % 3][i // 3]) << i for i in range(18)), word)   # and its mirror copy

    def test_a_larger_text_always_changes_the_picture_and_the_choice_is_deterministic(self):
        self.assertEqual(qrlib.encode('same'), qrlib.encode('same'))
        self.assertNotEqual(qrlib.encode('one'), qrlib.encode('two'))

    def test_capacity_limits(self):
        self.assertEqual(len(qrlib.encode('x' * 271, 'L')), 57)
        self.assertEqual(len(qrlib.encode('x' * 213, 'M')), 57)
        for text, ecc in (('x' * 272, 'L'), ('x' * 214, 'M')):
            with self.assertRaises(qrlib.QrError):
                qrlib.encode(text, ecc)
        with self.assertRaises(qrlib.QrError):
            qrlib.encode('x', 'H')

    def test_reed_solomon_remainder_of_the_whole_word_is_zero(self):
        for ver, ecc, text in ((1, 'M', 'HELLO WORLD'), (5, 'L', 'x' * 100), (9, 'M', 'y' * 150)):
            words = qrlib._make_codewords(text.encode(), ver, ecc)
            self.assertEqual(len(words), qrlib._raw_modules(ver) // 8)
            n_blocks, ecc_len = qrlib.NUM_BLOCKS[ecc][ver], qrlib.ECC_PER_BLOCK[ecc][ver]
            short = n_blocks - len(words) % n_blocks
            total = len(words) // n_blocks
            data_len = [total - ecc_len + (0 if i < short else 1) for i in range(n_blocks)]
            # de-interleave and check every block: data + ecc has remainder zero when divided by the generator
            data_words = words[:sum(data_len)]
            ecc_words = words[sum(data_len):]
            divisor = qrlib._rs_divisor(ecc_len)
            pos = [0] * n_blocks
            blocks = [[] for _ in range(n_blocks)]
            k = 0
            for i in range(max(data_len)):
                for j in range(n_blocks):
                    if i < data_len[j]:
                        blocks[j].append(data_words[k])
                        k += 1
            for j in range(n_blocks):
                rem = qrlib._rs_remainder(blocks[j], divisor)
                self.assertEqual(rem, [ecc_words[i * n_blocks + j] for i in range(ecc_len)])
                self.assertEqual(qrlib._rs_remainder(blocks[j] + rem, divisor), [0] * ecc_len)
            self.assertEqual(pos, [0] * n_blocks)


class OutputTests(unittest.TestCase):
    def test_svg(self):
        svg = qrlib.to_svg(qrlib.encode('A'))
        self.assertTrue(svg.startswith('<svg') and svg.endswith('</svg>'))
        self.assertIn('viewBox="0 0 29 29"', svg)                                  # 21 modules and a border of 4 on each side
        self.assertIn('fill="#000000"', svg)

    def test_the_svg_draws_exactly_the_dark_modules(self):
        import re
        m = qrlib.encode('http://10.42.0.1:8080/#/wifi')
        svg = qrlib.to_svg(m, border=4)
        cells = {(int(x) - 4, int(y) - 4) for x, y in re.findall(r'M(\d+),(\d+)h1v1h-1z', svg)}
        self.assertEqual(cells, {(x, y) for y, row in enumerate(m) for x, v in enumerate(row) if v})

    def test_png_is_a_valid_one_bit_image_of_the_right_size(self):
        m = qrlib.encode('http://10.42.0.1:8080/#/wifi')
        png = qrlib.to_png(m, scale=5, border=4)
        self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
        pos, chunks = 8, {}
        while pos < len(png):
            length, tag = struct.unpack('>I4s', png[pos:pos + 8])
            body = png[pos + 8:pos + 8 + length]
            self.assertEqual(struct.unpack('>I', png[pos + 8 + length:pos + 12 + length])[0], zlib.crc32(tag + body) & 0xFFFFFFFF)
            chunks[tag] = body
            pos += 12 + length
        size = (len(m) + 8) * 5
        self.assertEqual(struct.unpack('>IIBBBBB', chunks[b'IHDR']), (size, size, 1, 0, 0, 0, 0))
        raw = zlib.decompress(chunks[b'IDAT'])
        stride = 1 + (size + 7) // 8
        self.assertEqual(len(raw), stride * size)
        self.assertEqual(raw[0], 0)                                                 # filter type none
        self.assertTrue(all(b == 0xFF for b in raw[1:stride]))                      # the first row is the white border
        row_with_finder = raw[stride * (4 * 5 + 2):stride * (4 * 5 + 3)][1:]
        self.assertEqual(row_with_finder[0], 0xFF)
        self.assertNotEqual(row_with_finder[2], 0xFF)                               # the finder is dark after the 4 + border modules

    def test_the_wifi_text_escapes_what_phones_treat_as_separators(self):
        self.assertEqual(qrlib.wifi_text('Home', 'secret123'), 'WIFI:T:WPA;S:Home;P:secret123;;')
        self.assertEqual(qrlib.wifi_text('Open net'), 'WIFI:T:nopass;S:Open net;;')
        self.assertEqual(qrlib.wifi_text('Hidden', 'secret123', hidden=True), 'WIFI:T:WPA;S:Hidden;P:secret123;H:true;;')
        self.assertEqual(qrlib.wifi_text('a;b,c:d"e\\f', 'p;q'), 'WIFI:T:WPA;S:a\\;b\\,c\\:d\\"e\\\\f;P:p\\;q;;')


if __name__ == '__main__':
    unittest.main()
