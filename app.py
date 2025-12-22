import os
import logging
import io
import random
from flask import Flask, request, jsonify, send_from_directory, url_for, render_template
from pypdf import PdfReader
from PIL import Image

app = Flask(__name__)

# Configuration
UPLOAD_FOLDER = "uploads"
EXTRACTED_FOLDER = "images"
ALLOWED_EXTENSIONS = {"pdf"}
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB limit

# Flask JSON settings (pretty print + Unicode + no slash escaping)
app.config["JSONIFY_PRETTYPRINT_REGULAR"] = True
app.config["JSON_AS_ASCII"] = False  

# Ensure directories exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(EXTRACTED_FOLDER, exist_ok=True)

def generate_random_number(length=30):
    """Generate a random number string of given length."""
    return ''.join([str(random.randint(0, 9)) for _ in range(length)])

def extract_images_from_pdf(pdf_file_path: str, output_path: str):
    """
    Extract images from a PDF and rename:
      - user-img-<30digits>.<ext>
      - sign-img-<30digits>.<ext>
    But selection is NOT order-based anymore (fix for PDFs where signature comes first).
    Returns list of saved filenames where:
      [0] = user image, [1] = sign image (if found), others after.
    """
    try:
        import hashlib
        import math

        reader = PdfReader(pdf_file_path)
        seen_images = set()
        images = []  # each item: {"data":bytes, "ext":str, "w":int, "h":int, "ratio":float, "area":int, "has_alpha":bool}

        def has_transparency(pil_img: Image.Image) -> bool:
            return (
                pil_img.mode in ("RGBA", "LA")
                or (pil_img.mode == "P" and "transparency" in pil_img.info)
            )

        def is_portrait_like(w: int, h: int) -> bool:
            if h == 0:
                return False
            r = w / h
            # Your earlier ratios (common portrait crops)
            targets = [3/4, 4/5, 5/6]
            if any(math.isclose(r, t, rel_tol=0.06) for t in targets):
                return True
            # fallback portrait-ish
            return h > w and 0.60 <= r <= 0.95

        # 1) Collect unique images (stable hash)
        for page in reader.pages:
            for image in page.images:
                image_data = image.data
                image_hash = hashlib.md5(image_data).hexdigest()
                if image_hash in seen_images:
                    continue
                seen_images.add(image_hash)

                ext = os.path.splitext(image.name)[1].lower()

                # Convert JP2/JPEG2000 to PNG (same as your code)
                if ext in [".jp2", ".jpx"]:
                    try:
                        with Image.open(io.BytesIO(image_data)) as img:
                            if img.mode in ("RGBA", "P"):
                                img = img.convert("RGB")
                            image_bytes = io.BytesIO()
                            img.save(image_bytes, format="PNG")
                            image_data = image_bytes.getvalue()
                            ext = ".png"
                    except Exception as e:
                        logging.error(f"Failed to convert JP2 to PNG: {e}")
                        continue

                # Read meta
                try:
                    with Image.open(io.BytesIO(image_data)) as im:
                        w, h = im.size
                        ratio = (w / h) if h else 0.0
                        area = w * h
                        alpha = has_transparency(im)
                except Exception as e:
                    logging.error(f"Failed to read image meta: {e}")
                    w, h, ratio, area, alpha = 0, 0, 0.0, 0, False

                images.append({
                    "data": image_data,
                    "ext": ext if ext else ".png",
                    "w": w,
                    "h": h,
                    "ratio": ratio,
                    "area": area,
                    "has_alpha": alpha
                })

        if not images:
            return []

        # 2) Pick USER image:
        # Prefer portrait-ish JPG/JPEG; else choose largest area
        jpg_portraits = [
            i for i, im in enumerate(images)
            if im["ext"] in [".jpg", ".jpeg"] and is_portrait_like(im["w"], im["h"])
        ]
        if jpg_portraits:
            user_idx = max(jpg_portraits, key=lambda i: images[i]["area"])
        else:
            user_idx = max(range(len(images)), key=lambda i: images[i]["area"])

        user_area = images[user_idx]["area"]

        # 3) Pick SIGN image:
        # Prefer PNG (often signature), wide-ish, smaller than user, alpha helps
        remaining = [i for i in range(len(images)) if i != user_idx]
        sign_idx = None

        pngs = [i for i in remaining if images[i]["ext"] == ".png"]
        if pngs:
            def sign_score(i: int):
                im = images[i]
                wide = 1 if im["ratio"] > 1.4 else 0
                small = 1 if (user_area > 0 and im["area"] < user_area * 0.50) else 0
                alpha = 1 if im["has_alpha"] else 0
                # higher better; tie-break with smaller area
                return (wide + small + alpha, -im["area"])

            sign_idx = max(pngs, key=sign_score)
        elif remaining:
            # fallback: pick the smallest other image as sign
            sign_idx = min(remaining, key=lambda i: images[i]["area"])

        # 4) Save in correct order: user first, sign second, others after
        ordered = [user_idx]
        if sign_idx is not None:
            ordered.append(sign_idx)
        ordered += [i for i in range(len(images)) if i not in set(ordered)]

        extracted_files = []
        for pos, i in enumerate(ordered):
            ext = images[i]["ext"]
            random_number = generate_random_number(30)

            if pos == 0:
                image_filename = f"user-img-{random_number}{ext}"
            elif pos == 1:
                image_filename = f"sign-img-{random_number}{ext}"
            else:
                image_filename = f"{random_number}{ext}"

            file_path = os.path.join(output_path, image_filename)
            with open(file_path, "wb") as fp:
                fp.write(images[i]["data"])

            extracted_files.append(image_filename)

        return extracted_files

    except Exception as e:
        logging.error(f"Failed to extract images from {pdf_file_path}: {e}", exc_info=True)
        return []

# ✅ Helper response wrapper
def make_response(data: dict, status=200):
    if "Website" in data:
        data.pop("Website")
    data["Developer"] = "Rafsan The Developer"
    data["Website"] = "rafsanjane.com"
    return jsonify(data), status

@app.route("/")
def home():
    return make_response({"status": "Images Extractor Active"})

@app.route("/images", methods=["POST"])
def upload_file():
    """Handle file upload and extract images."""
    if "file" not in request.files:
        return make_response({"error": "No file part"}, 400)

    file = request.files["file"]

    if file.filename == "":
        return make_response({"error": "No selected file"}, 400)

    # Check file size
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)
    if file_size > MAX_FILE_SIZE:
        return make_response({"error": "File size exceeds 2 MB limit"}, 400)

    # Validate extension
    if file.filename.split(".")[-1].lower() not in ALLOWED_EXTENSIONS:
        return make_response({"error": "Invalid file type"}, 400)

    # Save PDF temporarily
    file_path = os.path.join(UPLOAD_FOLDER, f"{generate_random_number(30)}.pdf")
    file.save(file_path)

    extracted_images = extract_images_from_pdf(file_path, EXTRACTED_FOLDER)

    # Delete PDF after processing
    try:
        os.remove(file_path)
    except Exception as e:
        logging.error(f"Failed to delete PDF: {e}")

    if extracted_images:
        images_dict = {}
        if len(extracted_images) >= 1:
            images_dict["user-image"] = url_for("download_file", filename=extracted_images[0], _external=True)
        if len(extracted_images) >= 2:
            images_dict["sign-image"] = url_for("download_file", filename=extracted_images[1], _external=True)

        return make_response({
            "message": "Images extracted successfully",
            "totalImages": str(len(extracted_images)),
            "images": images_dict
        })

    return make_response({"message": "No images found in the PDF"})

@app.route("/images/<filename>")
def download_file(filename):
    """Serve extracted images."""
    return send_from_directory(EXTRACTED_FOLDER, filename)

@app.route("/upload")
def upload_page():
    return render_template("index.html")

if __name__ == "__main__":
    app.run(debug=True)
