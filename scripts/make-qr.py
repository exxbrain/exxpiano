#!/usr/bin/env python3
"""Make the QR codes of the setup network: one to join it, one to open the setup page. Files: PNG, SVG and a page to print (a sticker for the device).

  python3 scripts/make-qr.py --installation-id 0123456789ABCDEF

The installation ID is generated automatically on first boot and is shown by the Wi-Fi tab. Nothing here needs the internet or any package.
"""
import argparse
import html
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'pi'))
import qrlib  # noqa: E402
import wifilib  # noqa: E402

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>exxpiano: setup codes</title>
<style>
  body {{ font: 16px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; margin: 24px; color: #111; }}
  h1 {{ font-size: 1.3rem; margin: 0 0 4px; }}
  .sub {{ color: #555; margin: 0 0 18px; }}
  .codes {{ display: flex; flex-wrap: wrap; gap: 28px; }}
  .code {{ width: 260px; text-align: center; }}
  .code img {{ width: 260px; height: 260px; image-rendering: pixelated; }}
  .code b {{ display: block; font-size: 1.05rem; margin-top: 6px; }}
  .code span {{ display: block; color: #444; font-size: .9rem; overflow-wrap: anywhere; }}
  .hint {{ margin-top: 20px; color: #444; font-size: .9rem; max-width: 560px; }}
  @media print {{ body {{ margin: 8mm; }} .noprint {{ display: none; }} }}
</style></head><body>
<h1>exxpiano: Wi-Fi setup / Настройка Wi-Fi</h1>
<p class="sub">Installation <b>{install_id}</b> &middot; network <b>{ssid}</b> &middot; password <b>{password}</b></p>
<div class="codes">
  <div class="code"><img alt="Join the network" src="data:image/png;base64,{join_png}">
    <b>1. Join the network / Подключитесь к сети</b><span>{ssid}</span></div>
  <div class="code"><img alt="Open the setup page" src="data:image/png;base64,{page_png}">
    <b>2. Open the page / Откройте страницу</b><span>{page_url}</span></div>
</div>
<p class="hint">Scan the first code with the phone camera and accept the Wi-Fi network, then scan the second to open the settings
(the phone may open it by itself). Choose your home Wi-Fi, type its password, press Connect.<br>
Наведите камеру на первый код и подключитесь к сети, затем на второй, чтобы открыть настройки (телефон может открыть страницу сам).
Выберите домашнюю Wi-Fi, введите пароль и нажмите «Подключить».</p>
<p class="noprint"><button onclick="window.print()">Print / Печать</button></p>
</body></html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--installation-id', required=True, help='the persistent 16-character hexadecimal ID shown by the device')
    ap.add_argument('--password', default='exxpianosetup', help='WIFI_AP_PASSWORD of the device (default exxpianosetup)')
    ap.add_argument('--prefix', default='exxpiano-Setup')
    ap.add_argument('--address', default=wifilib.AP_ADDRESS)
    ap.add_argument('--port', type=int, default=8080)
    ap.add_argument('--out', default=os.path.join(ROOT, 'build', 'qr'))
    args = ap.parse_args()

    install_id = args.installation_id.upper()
    ssid = wifilib.ap_ssid(args.prefix, install_id)
    wifilib.validate_ssid(ssid)
    wifilib.validate_passphrase(args.password)
    # the code names its device: the page warns when it is opened on another one
    page_url = 'http://%s:%d/?d=%s#/wifi' % (args.address, args.port, install_id)
    codes = {'join-wifi': qrlib.wifi_text(ssid, args.password), 'open-page': page_url}
    os.makedirs(args.out, exist_ok=True)
    import base64
    images = {}
    for name, text in codes.items():
        matrix = qrlib.encode(text, 'M')
        png = qrlib.to_png(matrix, scale=10, border=4)
        images[name] = base64.b64encode(png).decode('ascii')
        with open(os.path.join(args.out, '%s-%s.png' % (name, ssid)), 'wb') as f:
            f.write(png)
        with open(os.path.join(args.out, '%s-%s.svg' % (name, ssid)), 'w', encoding='utf-8') as f:
            f.write(qrlib.to_svg(matrix))
    sheet = os.path.join(args.out, 'setup-codes-%s.html' % ssid)
    with open(sheet, 'w', encoding='utf-8') as f:
        f.write(PAGE.format(install_id=html.escape(install_id), ssid=html.escape(ssid), password=html.escape(args.password), page_url=html.escape(page_url),
                            join_png=images['join-wifi'], page_png=images['open-page']))
    print('Installation ID:', install_id, '| network:', ssid)
    print('Join code text :', codes['join-wifi'])
    print('Page code text :', page_url)
    for name in sorted(os.listdir(args.out)):
        if ssid in name:
            print('  ', os.path.join(args.out, name))
    print('Open the .html file in a browser to print the sticker.')


if __name__ == '__main__':
    main()
