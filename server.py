from flask import Flask, jsonify, request, Response
import os
import re
from io import BytesIO
from PIL import Image
import requests
from google import genai

app = Flask(__name__)

IMAGE_FOLDER = "images"
os.makedirs(IMAGE_FOLDER, exist_ok=True)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
VOICERSS_KEY = os.environ.get("VOICERSS_KEY")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


def normalize_topic(topic):
    topic = topic.strip().lower()
    topic = topic.replace(" ", "_")
    topic = re.sub(r"[^a-z0-9_()-]", "", topic)
    return topic


def convert_image_to_raw(img, raw_path):
    img = img.convert("RGB")
    img = img.resize((320, 240))

    with open(raw_path, "wb") as f:
        for y in range(240):
            for x in range(320):
                r, g, b = img.getpixel((x, y))
                rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                f.write(bytes([(rgb565 >> 8) & 0xFF, rgb565 & 0xFF]))


def make_gray_raw(raw_path):
    img = Image.new("RGB", (320, 240), (96, 96, 96))
    convert_image_to_raw(img, raw_path)
    return raw_path


def build_image_prompt(topic):
    return (
        f"Create a clean, detailed, realistic image of {topic}. "
        f"Single clear main subject, centered composition, visually appealing, "
        f"good lighting, no text, no watermark, no logo, suitable for a small 320x240 display."
    )


def build_text_prompt(topic):
    return f"In 5 words only: {topic}"


def generate_gemini_image_raw(topic, force_refresh=False):
    safe = normalize_topic(topic)
    raw_path = os.path.join(IMAGE_FOLDER, safe + ".raw")

    if os.path.exists(raw_path) and not force_refresh:
        print("Using cached RAW image:", raw_path)
        return raw_path, "cache"

    if not client:
        raise RuntimeError("GEMINI_API_KEY not set")

    prompt = build_image_prompt(topic)
    print("Generating Gemini image for:", topic)
    print("Prompt:", prompt)

    response = client.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=prompt,
    )

    # Robust extraction across SDK response layouts
    pil_img = None

    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline_data = getattr(part, "inline_data", None)
            if inline_data is not None:
                data = getattr(inline_data, "data", None)
                mime_type = getattr(inline_data, "mime_type", None)
                if data:
                    img_bytes = data if isinstance(data, (bytes, bytearray)) else None
                    if img_bytes is None and isinstance(data, str):
                        import base64
                        img_bytes = base64.b64decode(data)
                    if img_bytes:
                        print("Gemini image mime_type:", mime_type)
                        pil_img = Image.open(BytesIO(img_bytes))
                        break
        if pil_img is not None:
            break

    # Fallback for SDKs exposing top-level parts
    if pil_img is None:
        top_parts = getattr(response, "parts", None) or []
        for part in top_parts:
            inline_data = getattr(part, "inline_data", None)
            if inline_data is not None:
                data = getattr(inline_data, "data", None)
                mime_type = getattr(inline_data, "mime_type", None)
                if data:
                    img_bytes = data if isinstance(data, (bytes, bytearray)) else None
                    if img_bytes is None and isinstance(data, str):
                        import base64
                        img_bytes = base64.b64decode(data)
                    if img_bytes:
                        print("Gemini image mime_type:", mime_type)
                        pil_img = Image.open(BytesIO(img_bytes))
                        break

    if pil_img is None:
        raise RuntimeError(f"Gemini returned no image part. Raw response type: {type(response)}")

    convert_image_to_raw(pil_img, raw_path)
    print("RAW image created:", raw_path)
    return raw_path, "generated"


def get_short_text(topic):
    if not client:
        raise RuntimeError("GEMINI_API_KEY not set")

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=build_text_prompt(topic),
    )

    text = (getattr(response, "text", "") or "").strip()
    if not text:
        raise RuntimeError("Gemini returned empty text")

    return text


@app.route("/")
def home():
    return "OK"


@app.route("/image/<topic>")
def image(topic):
    refresh = request.args.get("refresh", "0") == "1"

    try:
        path, source = generate_gemini_image_raw(topic, force_refresh=refresh)
        print("IMAGE SOURCE:", source)
    except Exception as e:
        print("IMAGE ERROR:", str(e))
        safe = normalize_topic(topic)
        path = os.path.join(IMAGE_FOLDER, safe + ".raw")
        path = make_gray_raw(path)
        print("IMAGE SOURCE: gray-fallback")

    file_size = os.path.getsize(path)

    def generate():
        with open(path, "rb") as f:
            while True:
                chunk = f.read(4096)
                if not chunk:
                    break
                yield chunk

    return Response(
        generate(),
        mimetype="application/octet-stream",
        headers={
            "Content-Length": str(file_size),
            "Cache-Control": "no-cache"
        }
    )


@app.route("/full/<topic>")
def full(topic):
    try:
        return jsonify({"text": get_short_text(topic)})
    except Exception as e:
        print("FULL ERROR:", str(e))
        return jsonify({"error": str(e)}), 500


@app.route("/tts")
def tts():
    text = request.args.get("text", "").strip()

    if not text:
        return Response("Missing text", status=400)

    if not VOICERSS_KEY:
        return Response("VOICERSS_KEY not set", status=500)

    url = (
        "https://api.voicerss.org/?key=" + VOICERSS_KEY +
        "&hl=en-us&src=" + requests.utils.quote(text) +
        "&f=8khz_8bit_mono_pcm&codec=PCM"
    )

    r = requests.get(url, stream=True, timeout=30)
    r.raise_for_status()

    return Response(r.iter_content(512), content_type="application/octet-stream")


@app.route("/debug_image/<topic>")
def debug_image(topic):
    safe = normalize_topic(topic)
    raw_path = os.path.join(IMAGE_FOLDER, safe + ".raw")
    return jsonify({
        "topic": topic,
        "prompt": build_image_prompt(topic),
        "cached_exists": os.path.exists(raw_path),
        "raw_path": raw_path,
        "gemini_key_present": bool(GEMINI_API_KEY),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
