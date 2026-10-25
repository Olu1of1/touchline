"""Optional neural voice via Azure AI Speech (REST, no SDK needed).

    AZURE_SPEECH_KEY
    AZURE_SPEECH_REGION      e.g. uksouth, westeurope
    AZURE_SPEECH_VOICE       optional, default en-GB-RyanNeural

Excitement is expressed with SSML prosody (rate and pitch), which every neural
voice supports. Audio is cached on disk so a replayed match costs nothing.
"""

import hashlib
import os
import urllib.request
from xml.sax.saxutils import escape

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".cache", "tts")


def available():
    return bool(os.environ.get("AZURE_SPEECH_KEY") and os.environ.get("AZURE_SPEECH_REGION"))


def voice():
    return os.environ.get("AZURE_SPEECH_VOICE", "en-GB-RyanNeural")


def ssml(text, excite=0.0, rate=1.0):
    if excite >= 1.5:
        pros = {"rate": "+14%", "pitch": "+12%", "volume": "+10%"}
    elif excite >= 0.8:
        pros = {"rate": "+7%", "pitch": "+5%", "volume": "+0%"}
    else:
        pros = {"rate": "+0%", "pitch": "+0%", "volume": "+0%"}
    base = int(round((rate - 1.0) * 100))
    r = int(pros["rate"].rstrip("%")) + base
    v = voice()
    lang = "-".join(v.split("-")[:2])
    return (f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{lang}">'
            f'<voice name="{v}"><prosody rate="{r:+d}%" pitch="{pros["pitch"]}" volume="{pros["volume"]}">'
            f"{escape(text)}</prosody></voice></speak>")


def synthesize(text, excite=0.0, rate=1.0):
    """Return MP3 bytes for the line (cached)."""
    doc = ssml(text, excite, rate)
    key = hashlib.sha1(doc.encode("utf-8")).hexdigest()
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, key + ".mp3")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    region = os.environ["AZURE_SPEECH_REGION"]
    req = urllib.request.Request(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
        data=doc.encode("utf-8"),
        headers={
            "Ocp-Apim-Subscription-Key": os.environ["AZURE_SPEECH_KEY"],
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
            "User-Agent": "touchline",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        audio = r.read()
    with open(path, "wb") as f:
        f.write(audio)
    return audio
