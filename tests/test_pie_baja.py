"""El pie de baja: sin firma, y como texto con enlace en la versión HTML.

    python tests/test_pie_baja.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")
from app import mailer  # noqa: E402

fallas = []


def ver(c, q):
    print(("ok  " if c else "XX  ") + q)
    if not c:
        fallas.append(q)


url = "https://b2k.gmarketing.co/u/ABC123"
pie = mailer._pie_de_baja("https://b2k.gmarketing.co", "ABC123", html=False)
ver("gmarketing.co ·" not in pie and "\n—\ngmarketing" not in pie, "el pie no lleva la firma")
ver(url in pie and "respóndeme" in pie, "el pie de texto trae el enlace y la opción de responder")

enviado = {}


class SMTPFalso:
    def __init__(self, *a, **k): pass
    def ehlo(self): pass
    def starttls(self, **k): pass
    def login(self, *a): pass
    def send_message(self, msg): enviado["msg"] = msg
    def quit(self): pass
    def close(self): pass


mailer.smtplib.SMTP_SSL = SMTPFalso
cfg = {"from_email": "gerencia@gaptravelcrm.com", "from_name": "", "host": "x", "port": 465,
       "security": "ssl", "username": "u", "password": "p"}
mailer._enviar_sincrono(cfg, "cli@x.co", "Hola", "Hola Ana,\n\nUna idea.\n\n¿Hablamos?" + pie,
                        None, "<id@x>", url)
msg = enviado["msg"]
html = next((p.get_content() for p in msg.walk() if p.get_content_type() == "text/html"), "")
texto = next((p.get_content() for p in msg.walk() if p.get_content_type() == "text/plain"), "")
ver(bool(html), "el correo sale también en HTML")
ver(f'href="{url}"' in html and f">{url}<" not in html and url not in html.replace(f'href="{url}"', ""),
    "en HTML el enlace es un texto clickeable, no la dirección a la vista")
ver("Hola Ana" in html and "<p" in html, "el HTML respeta los párrafos del correo")
ver(url in texto, "la versión de texto conserva la dirección (no tiene enlaces)")
ver(msg["List-Unsubscribe"] == f"<{url}>", "Gmail sigue recibiendo su botón de baja")
print("TODO BIEN" if not fallas else f"{len(fallas)} FALLAS")
sys.exit(1 if fallas else 0)
