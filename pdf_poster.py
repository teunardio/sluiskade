"""
A4 poster generator voor de sluiswachter-QR-code.

Output: een vriendelijke één-pagina PDF in onze foam/water huisstijl,
klaar om uit te printen en op een raam van het sluiswachtershuis te
plakken. Ontworpen voor scannen vanaf ongeveer 1-2 meter afstand.

Layout (top to bottom):
    - SLUISKADE wordmark + golf-decoratie
    - "Hallo sluiswachter!" hoofdtitel
    - Korte uitleg waarom we ze nodig hebben
    - Grote QR-code (~75mm) in een lichtblauw kader
    - "Scan, kies een foto, klaar." instructie
    - Bedankje
    - Footer met contactadres + domein

Reportlab gebruikt punten (1pt = 1/72 inch). A4 = 595 x 842 pt.
Voor leesbaarheid gebruiken we mm via de units helper.
"""
from __future__ import annotations

import io

import qrcode
from reportlab.lib.colors import HexColor, Color
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

# Brand kleuren, gespiegeld aan de site
DEEP = HexColor("#0c4a6e")
WATER = HexColor("#0ea5e9")
FOAM = HexColor("#e0f2fe")
PAPER = HexColor("#f8fafc")
MUTED = HexColor("#64748b")
INK = HexColor("#0f172a")
BORDER = HexColor("#bae6fd")


def _qr_png_bytes(url: str, *, box_size: int = 14) -> bytes:
    """Genereer een hoog-resolutie zwart-op-wit QR PNG.

    error_correction=H staat 30% data-corruptie toe — handig voor een
    poster die in een vochtig sluiswachtershuis hangt. box_size=14
    geeft op A4 een scherp resultaat ook van een meter afstand.
    """
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=box_size,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _draw_wave(c: canvas.Canvas, x: float, y: float, width: float) -> None:
    """Decoratieve golf-lijn in WATER kleur, gelijk aan het site-icoon."""
    c.setStrokeColor(WATER)
    c.setLineWidth(1.5)
    c.setLineCap(1)  # rounded
    p = c.beginPath()
    p.moveTo(x, y)
    # Soft sinusoidal wave, vier toppen over de breedte
    seg = width / 8
    p.curveTo(x + seg, y + 4, x + 2 * seg, y - 4, x + 3 * seg, y)
    p.curveTo(x + 4 * seg, y + 4, x + 5 * seg, y - 4, x + 6 * seg, y)
    p.curveTo(x + 7 * seg, y + 4, x + 8 * seg, y - 4, x + 8 * seg, y)
    c.drawPath(p, stroke=1, fill=0)


def generate_qr_poster(qr_url: str, *, contact_email: str = "beheer@sluiskade.com") -> bytes:
    """Render de A4 QR-poster en return de bytes.

    qr_url: de volle URL die in de QR komt (incl. signed token), bv.
            https://sluiskade.com/sluis?t=<token>
    contact_email: voet-adres voor vragen. Default beheer@sluiskade.com.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4

    # ---- Achtergrond: zachte foam-kleur over de hele pagina --------------
    c.setFillColor(FOAM)
    c.rect(0, 0, page_w, page_h, fill=1, stroke=0)

    # ---- Witte kaart met afgeronde hoeken --------------------------------
    margin = 18 * mm
    card_x = margin
    card_y = margin
    card_w = page_w - 2 * margin
    card_h = page_h - 2 * margin
    c.setFillColor(HexColor("#ffffff"))
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.8)
    c.roundRect(card_x, card_y, card_w, card_h, 10 * mm, fill=1, stroke=1)

    # ---- Brand wordmark ---------------------------------------------------
    brand_y = page_h - margin - 22 * mm
    c.setFont("Helvetica-Bold", 24)
    c.setFillColor(DEEP)
    c.drawCentredString(page_w / 2, brand_y, "SLUIS")
    # "KADE" in WATER kleur ernaast - we tekenen 'm in twee delen voor accent
    sluis_width = c.stringWidth("SLUIS", "Helvetica-Bold", 24)
    kade_x = page_w / 2 - sluis_width / 2 + sluis_width
    c.setFillColor(WATER)
    c.drawString(kade_x, brand_y, "KADE")
    # Correctie: drawCentredString centreerde SLUIS, dus we moeten 't woord
    # in z'n geheel opnieuw uitlijnen. Wis en hertken.
    c.setFillColor(HexColor("#ffffff"))
    c.rect(margin + 5, brand_y - 4, card_w - 10, 32, fill=1, stroke=0)
    # Bouw "SLUISKADE" als één gecentreerd label met twee kleuren
    full_text = "SLUISKADE"
    full_width = c.stringWidth(full_text, "Helvetica-Bold", 28)
    start_x = (page_w - full_width) / 2
    c.setFont("Helvetica-Bold", 28)
    c.setFillColor(DEEP)
    c.drawString(start_x, brand_y, "SLUIS")
    c.setFillColor(WATER)
    c.drawString(start_x + c.stringWidth("SLUIS", "Helvetica-Bold", 28), brand_y, "KADE")

    # Tagline onder de brand
    c.setFont("Helvetica", 9)
    c.setFillColor(MUTED)
    tagline = "BOUWFOTO'S VANUIT JOUW UITZICHT"
    c.drawCentredString(page_w / 2, brand_y - 6 * mm, tagline)

    # Golfje als visuele scheider
    wave_y = brand_y - 12 * mm
    wave_width = 50 * mm
    _draw_wave(c, (page_w - wave_width) / 2, wave_y, wave_width)

    # ---- Hoofdtitel -------------------------------------------------------
    title_y = page_h - margin - 55 * mm
    c.setFont("Helvetica-Bold", 30)
    c.setFillColor(DEEP)
    c.drawCentredString(page_w / 2, title_y, "Hallo sluiswachter!")

    # ---- Body tekst -------------------------------------------------------
    body_lines = [
        "Naast jullie sluis verrijst Sluiskade,",
        "een nieuw buurtje met een handvol huizen aan het water.",
        "",
        "De toekomstige bewoners volgen graag hoe hun huis vorm krijgt.",
        "Jullie hebben verreweg het mooiste uitzicht op de bouw,",
        "veel mooier dan elke bouwcamera ooit kan vastleggen.",
        "",
        "Zou je af en toe een foto willen delen?",
    ]
    line_height = 5.5 * mm
    body_start_y = title_y - 14 * mm
    c.setFont("Helvetica", 12)
    c.setFillColor(INK)
    for i, line in enumerate(body_lines):
        c.drawCentredString(page_w / 2, body_start_y - i * line_height, line)

    # ---- QR-code in een lichtblauw kader ---------------------------------
    qr_size = 70 * mm
    qr_x = (page_w - qr_size) / 2
    qr_y = card_y + 70 * mm

    # Kader om de QR
    frame_pad = 6 * mm
    c.setFillColor(FOAM)
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.8)
    c.roundRect(
        qr_x - frame_pad,
        qr_y - frame_pad,
        qr_size + 2 * frame_pad,
        qr_size + 2 * frame_pad,
        4 * mm,
        fill=1,
        stroke=1,
    )

    # Witte achtergrond direct onder QR voor max contrast
    c.setFillColor(HexColor("#ffffff"))
    c.rect(qr_x, qr_y, qr_size, qr_size, fill=1, stroke=0)

    qr_img = ImageReader(io.BytesIO(_qr_png_bytes(qr_url)))
    c.drawImage(qr_img, qr_x, qr_y, width=qr_size, height=qr_size, preserveAspectRatio=True)

    # ---- Instructie onder QR ---------------------------------------------
    instr_y = qr_y - 12 * mm
    c.setFont("Helvetica-Bold", 14)
    c.setFillColor(DEEP)
    c.drawCentredString(page_w / 2, instr_y, "Scan, kies een foto, klaar.")

    # Sub-instructie
    c.setFont("Helvetica", 10)
    c.setFillColor(MUTED)
    c.drawCentredString(
        page_w / 2,
        instr_y - 5 * mm,
        "Werkt op elke smartphone, geen app nodig.",
    )

    # ---- Bedankje --------------------------------------------------------
    thanks_y = instr_y - 16 * mm
    c.setFont("Helvetica-Oblique", 13)
    c.setFillColor(WATER)
    c.drawCentredString(page_w / 2, thanks_y, "We zijn jullie eeuwig dankbaar.")

    # ---- Footer ----------------------------------------------------------
    footer_y = card_y + 12 * mm
    c.setFont("Helvetica", 9)
    c.setFillColor(MUTED)
    c.drawCentredString(
        page_w / 2,
        footer_y + 4 * mm,
        f"Vragen of liever niet? Mail {contact_email}.",
    )
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(DEEP)
    c.drawCentredString(page_w / 2, footer_y - 1 * mm, "sluiskade.com")

    c.showPage()
    c.save()
    return buf.getvalue()
