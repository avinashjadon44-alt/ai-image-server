from flask import Flask, jsonify, request, Response
import os
import re
import base64
from io import BytesIO

from PIL import Image
import requests
from google import genai

app = Flask(__name__)

IMAGE_FOLDER = "images"
os.makedirs(IMAGE_FOLDER, exist_ok=True)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
VOICERSS_KEY = os.environ.get("VOICERSS_KEY")

IMAGE_MODEL = "gemini-2.5-flash-image"
TEXT_MODEL = "gemini-2.5-flash"

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


# -------------------------
# HELPERS
# -------------------------
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


def extract_pil_image_from_response(response):
    # candidates -> content -> parts
    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline_data = getattr(part, "inline_data", None)
            if inline_data is not None:
                data = getattr(inline_data, "data", None)
                mime_type = getattr(inline_data, "mime_type", None)
                print("Found candidate inline_data mime_type:", mime_type)

                if isinstance(data, (bytes, bytearray)):
                    return Image.open(BytesIO(data))

                if isinstance(data, str):
                    return Image.open(BytesIO(base64.b64decode(data)))

    # top-level parts
    top_parts = getattr(response, "parts", None) or []
    for part in top_parts:
        inline_data = getattr(part, "inline_data", None)
        if inline_data is not None:
            data = getattr(inline_data, "data", None)
            mime_type = getattr(inline_data, "mime_type", None)
            print("Found top-level inline_data mime_type:", mime_type)

            if isinstance(data, (bytes, bytearray)):
                return Image.open(BytesIO(data))

            if isinstance(data, str):
                return Image.open(BytesIO(base64.b64decode(data)))

    return None


def classify_exception(exc):
    msg = str(exc)
    lowered = msg.lower()

    info = {
        "type": type(exc).__name__,
        "message": msg,
        "is_quota_error": False,
        "is_rate_limit": False,
        "retryable": False,
    }

    if "resource_exhausted" in lowered or "quota exceeded" in lowered or "429" in lowered:
        info["is_quota_error"] = True
        info["is_rate_limit"] = True
        info["retryable"] = True

    return info


# -------------------------
# GEMINI IMAGE
# -------------------------
def generate_gemini_image_raw(topic, force_refresh=False):
    safe = normalize_topic(topic)
    raw_path = os.path.join(IMAGE_FOLDER, safe + ".raw")

    if os.path.exists(raw_path) and not force_refresh:
        print("IMAGE SOURCE: cache")
        print("Using cached RAW image:", raw_path)
        return raw_path, "cache"

    if not client:
        raise RuntimeError("GEMINI_API_KEY not set")

    prompt = build_image_prompt(topic)
    print("Generating Gemini image for:", topic)
    print("Image model:", IMAGE_MODEL)
    print("Prompt:", prompt)

    response = client.models.generate_content(
        model=IMAGE_MODEL,
        contents=prompt,
    )

    print("Gemini image response type:", type(response))

    pil_img = extract_pil_image_from_response(response)
    if pil_img is None:
        raise RuntimeError("Gemini returned no image part")

    convert_image_to_raw(pil_img, raw_path)
    print("IMAGE SOURCE: generated")
    print("RAW image created:", raw_path)
    return raw_path, "generated"


# -------------------------
# GEMINI TEXT
# -------------------------
def get_short_text(topic):
    if not client:
        raise RuntimeError("GEMINI_API_KEY not set")

    response = client.models.generate_content(
        model=TEXT_MODEL,
        contents=build_text_prompt(topic),
    )

    text = (getattr(response, "text", "") or "").strip()
    if not text:
        raise RuntimeError("Gemini returned empty text")

    return text


# -------------------------
# ROUTES
# -------------------------
@app.route("/")
def home():
    return "OK"


@app.route("/image/<topic>")
def image(topic):
    refresh = request.args.get("refresh", "0") == "1"
    fallback = request.args.get("fallback", "1") == "1"

    try:
        path, source = generate_gemini_image_raw(topic, force_refresh=refresh)
        print("Final image source:", source)

    except Exception as e:
        err = classify_exception(e)
        print("IMAGE ERROR:", err["message"])

        if not fallback:
            status = 429 if err["is_quota_error"] else 500
            return jsonify({
                "ok": False,
                "error": err["message"],
                "error_type": err["type"],
                "is_quota_error": err["is_quota_error"],
                "retryable": err["retryable"],
                "model": IMAGE_MODEL,
                "topic": topic,
            }), status

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


@app.route("/test_image/<topic>")
def test_image(topic):
    try:
        path, source = generate_gemini_image_raw(topic, force_refresh=True)
        return jsonify({
            "ok": True,
            "source": source,
            "path": path,
            "model": IMAGE_MODEL,
            "topic": topic,
        })
    except Exception as e:
        err = classify_exception(e)
        status = 429 if err["is_quota_error"] else 500
        return jsonify({
            "ok": False,
            "error": err["message"],
            "error_type": err["type"],
            "is_quota_error": err["is_quota_error"],
            "is_rate_limit": err["is_rate_limit"],
            "retryable": err["retryable"],
            "model": IMAGE_MODEL,
            "topic": topic,
            "prompt": build_image_prompt(topic),
        }), status


@app.route("/full/<topic>")
def full(topic):
    try:
        return jsonify({
            "text": get_short_text(topic),
            "model": TEXT_MODEL
        })
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
        "image_model": IMAGE_MODEL,
        "text_model": TEXT_MODEL,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
