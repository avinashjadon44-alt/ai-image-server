from flask import Flask, send_file, jsonify, request, Response
import os
import re
import requests
from PIL import Image

app = Flask(__name__)

IMAGE_FOLDER = "images"
os.makedirs(IMAGE_FOLDER, exist_ok=True)

GEMINI_KEY = os.environ.get("GEMINI_KEY")
VOICERSS_KEY = os.environ.get("VOICERSS_KEY")

HEADERS = {
    "User-Agent": "ESP32-Image-Backend/1.0"
}


def normalize_topic(topic):
    topic = topic.strip().lower()
    topic = topic.replace(" ", "_")
    topic = re.sub(r"[^a-z0-9_()-]", "", topic)
    return topic


def wikipedia_thumbnail_url(topic):
    api_url = "https://en.wikipedia.org/w/api.php"

    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": topic,
        "gsrlimit": 1,
        "prop": "pageimages",
        "piprop": "thumbnail",
        "pithumbsize": 640,
        "format": "json"
    }

    res = requests.get(api_url, params=params, headers=HEADERS, timeout=20)
    res.raise_for_status()
    data = res.json()

    pages = data.get("query", {}).get("pages", {})
    for _, page in pages.items():
        thumb = page.get("thumbnail", {})
        source = thumb.get("source")
        if source:
            return source

    return None


def convert_image_to_raw(img, raw_path):
    img = img.convert("RGB")
    img = img.resize((320, 240))

    with open(raw_path, "wb") as f:
        for y in range(240):
            for x in range(320):
                r, g, b = img.getpixel((x, y))
                rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                f.write(bytes([(rgb565 >> 8) & 0xFF, rgb565 & 0xFF]))


def fetch_and_convert(topic):
    safe_topic = normalize_topic(topic)
    raw_path = os.path.join(IMAGE_FOLDER, safe_topic + ".raw")
    jpg_path = os.path.join(IMAGE_FOLDER, safe_topic + ".jpg")

    if os.path.exists(raw_path):
        print("Using cached raw:", raw_path)
        return raw_path

    print("Fetching image for:", topic)

    try:
        thumb_url = wikipedia_thumbnail_url(topic)

        if not thumb_url:
            raise Exception("No image found")

        img_res = requests.get(thumb_url, headers=HEADERS, timeout=20)
        img_res.raise_for_status()

        with open(jpg_path, "wb") as f:
            f.write(img_res.content)

        img = Image.open(jpg_path)
        convert_image_to_raw(img, raw_path)

        return raw_path

    except Exception as e:
        print("Image error:", e)

        img = Image.new("RGB", (320, 240), (96, 96, 96))
        convert_image_to_raw(img, raw_path)

        return raw_path


@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "routes": ["/image/<topic>", "/gemini/<topic>", "/tts?text=..."]
    })


@app.route("/image/<topic>")
def get_image(topic):
    try:
        path = fetch_and_convert(topic)
        return send_file(path, mimetype="application/octet-stream")
    except Exception as e:
        return str(e), 500


@app.route("/gemini/<topic>")
def gemini_text(topic):
    if not GEMINI_KEY:
        return jsonify({"error": "GEMINI_KEY not set"}), 500

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=" + GEMINI_KEY

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": "In 5 words: " + topic}
                ]
            }
        ]
    }

    headers = {
        "Content-Type": "application/json"
    }

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=20)
        res.raise_for_status()
        data = res.json()

        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return jsonify({"text": text})

    except Exception as e:
        print("Gemini error:", e)
        return jsonify({"error": str(e)}), 500


@app.route("/tts")
def tts():
    if not VOICERSS_KEY:
        return "VOICERSS_KEY not set", 500

    text = request.args.get("text", "")
    text = text.replace("%20", " ")
    text = text.strip()

    if not text:
        return "Missing text", 400

    url = (
        "https://api.voicerss.org/"
        "?key=" + VOICERSS_KEY +
        "&hl=en-us" +
        "&src=" + requests.utils.quote(text) +
        "&f=8khz_8bit_mono_pcm" +
        "&codec=PCM"
    )

    try:
        r = requests.get(url, stream=True, timeout=30)
        r.raise_for_status()

        return Response(
            r.iter_content(chunk_size=512),
            content_type="application/octet-stream"
        )

    except Exception as e:
        print("TTS error:", e)
        return str(e), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
