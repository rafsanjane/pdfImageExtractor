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
    """Extract images from a PDF and rename first two images as user-img and sign-img with 30-digit random numbers."""
    try:
        reader = PdfReader(pdf_file_path)
        seen_images = set()
        extracted_files = []
        image_count = 0

        for page in reader.pages:
            for image in page.images:
                image_data = image.data
                image_hash = hash(image_data)

                if image_hash in seen_images:
                    continue

                seen_images.add(image_hash)
                ext = os.path.splitext(image.name)[1].lower()

                # Convert JP2/JPEG2000 to PNG
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

                # Naming logic with 30-digit random numbers
                random_number = generate_random_number(30)
                if image_count == 0:
                    image_filename = f"user-img-{random_number}{ext}"
                elif image_count == 1:
                    image_filename = f"sign-img-{random_number}{ext}"
                else:
                    image_filename = f"{random_number}{ext}"

                file_path = os.path.join(output_path, image_filename)
                with open(file_path, "wb") as fp:
                    fp.write(image_data)

                extracted_files.append(image_filename)
                image_count += 1

        return extracted_files

    except Exception as e:
        logging.error(f"Failed to extract images from {pdf_file_path}: {e}")
        return []

# ✅ Helper response wrapper
def make_response(data: dict, status=200):
    """Attach TG_Channel at the end and return JSON response with proper formatting."""
    if "TG_Channel" in data:
        data.pop("TG_Channel")
    data["TG_Channel"] = "@UNKNOWN_X_1337_BOT"
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
