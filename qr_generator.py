import os

import qrcode
from PIL import Image

LOGO_PATH = os.path.join(os.path.dirname(__file__), "hades_logo.png")
QR_DIR = os.path.join(os.path.dirname(__file__), "qr_cache")

# Final output size in pixels
QR_OUTPUT_SIZE = 300


def _ensure_qr_dir() -> None:
    os.makedirs(QR_DIR, exist_ok=True)


def generate_qr(wallet_address: str, coin: str) -> str:
    """Generate a compact QR code PNG with the HADES logo in the center.

    Returns the file path to the generated image.
    Caches by coin name so we only generate once per wallet.
    """
    _ensure_qr_dir()
    out_path = os.path.join(QR_DIR, f"{coin.lower()}_qr.png")

    # Delete old cache so changes take effect
    if os.path.exists(out_path):
        os.remove(out_path)

    # Generate a compact QR — small box_size, tight border
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=6,
        border=2,
    )
    qr.add_data(wallet_address)
    qr.make(fit=True)

    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")

    # Resize QR to target output size
    qr_img = qr_img.resize((QR_OUTPUT_SIZE, QR_OUTPUT_SIZE), Image.NEAREST)

    # Overlay the HADES logo in the center
    if os.path.exists(LOGO_PATH):
        logo = Image.open(LOGO_PATH).convert("RGBA")

        # Logo ~22% of QR size — keeps it scannable
        logo_size = int(QR_OUTPUT_SIZE * 0.22)
        logo = logo.resize((logo_size, logo_size), Image.LANCZOS)

        # Center the square logo directly on the QR
        lx = (QR_OUTPUT_SIZE - logo_size) // 2
        ly = (QR_OUTPUT_SIZE - logo_size) // 2
        qr_img.paste(logo, (lx, ly), logo)

    qr_img.save(out_path)
    return out_path
