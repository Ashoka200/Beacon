"""media_studio.py — TIER 3. Turns an approved creative brief into REAL
rendered media by calling a best-in-class generation model (image or video)
through its API. It is the governed wrapper around a rented model: Sentinel
screens the brief, output is client-gated, and compliance metadata rides along
server-side.

Capabilities:
  * text-to-image (count 1..4 per request)
  * image-to-image refinement: client uploads their own photo (base64 data
    URL in payload.reference_image) and Gemini restyles it per the prompt.

Provider wiring is a seam, configured by env so no key = honest stub mode:
  BEACON_IMAGE_PROVIDER  + BEACON_IMAGE_API_KEY   (e.g. "gemini")
  BEACON_VIDEO_PROVIDER  + BEACON_VIDEO_API_KEY   (e.g. "veo" / "runway")
"""
from __future__ import annotations
import os, base64, re
from base import Agent
from contracts import Capability, AgentRequest, AgentResponse

IMAGE_PROVIDER = os.environ.get("BEACON_IMAGE_PROVIDER", "").strip().lower()
VIDEO_PROVIDER = os.environ.get("BEACON_VIDEO_PROVIDER", "").strip().lower()
IMAGE_API_KEY = (os.environ.get("BEACON_IMAGE_API_KEY")
                 or os.environ.get("GEMINI_API_KEY") or "").strip()
GEMINI_IMAGE_MODEL = os.environ.get("BEACON_GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")

_DATA_URL = re.compile(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", re.S)


def _decode_data_url(url: str):
    """data:image/...;base64,xxx -> (bytes, mime) or (None, None)."""
    m = _DATA_URL.match(url or "")
    if not m:
        return None, None
    try:
        return base64.b64decode(m.group(2)), m.group(1)
    except Exception:
        return None, None


class MediaStudio(Agent):
    name, tier = "MediaStudio", 3
    serves = {Capability.GENERATE_MEDIA}

    # --- generation seams -----------------------------------------------
    def _generate_image(self, prompt: str, ref_bytes=None, ref_mime=None) -> dict:
        if not IMAGE_PROVIDER:
            return {"kind": "image", "status": "no_provider_configured",
                    "note": "Set BEACON_IMAGE_PROVIDER + BEACON_IMAGE_API_KEY to render real images.",
                    "prompt": prompt}
        if IMAGE_PROVIDER == "gemini":
            return self._gemini_image(prompt, ref_bytes, ref_mime)
        return {"kind": "image", "status": "unknown_provider",
                "provider": IMAGE_PROVIDER, "prompt": prompt}

    def _gemini_image(self, prompt: str, ref_bytes=None, ref_mime=None) -> dict:
        """Render with Google Gemini. With ref_bytes, runs image-to-image
        refinement of the client's own photo. Returns an inline data URL."""
        if not IMAGE_API_KEY:
            return {"kind": "image", "status": "no_api_key",
                    "note": "Set BEACON_IMAGE_API_KEY to your Google AI Studio key.", "prompt": prompt}
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=IMAGE_API_KEY)
            if ref_bytes:
                contents = [types.Part.from_bytes(data=ref_bytes, mime_type=ref_mime or "image/jpeg"),
                            prompt]
            else:
                contents = prompt
            resp = client.models.generate_content(model=GEMINI_IMAGE_MODEL, contents=contents)
            for part in resp.parts:
                if getattr(part, "inline_data", None) is not None:
                    raw = part.inline_data.data
                    mime = getattr(part.inline_data, "mime_type", "image/png")
                    b64 = base64.b64encode(raw).decode() if isinstance(raw, (bytes, bytearray)) else raw
                    return {"kind": "image", "status": "rendered", "provider": "gemini",
                            "model": GEMINI_IMAGE_MODEL,
                            "refined_from_upload": bool(ref_bytes),
                            "data_url": f"data:{mime};base64,{b64}", "prompt": prompt}
            return {"kind": "image", "status": "no_image_returned",
                    "note": "Model returned no image (it may have declined the prompt).", "prompt": prompt}
        except Exception as e:  # never crash the mesh on a provider hiccup
            return {"kind": "image", "status": "error", "provider": "gemini",
                    "note": f"{type(e).__name__}: {e}", "prompt": prompt}

    def _generate_video(self, prompt: str) -> dict:
        if not VIDEO_PROVIDER:
            return {"kind": "video", "status": "no_provider_configured",
                    "note": "Set BEACON_VIDEO_PROVIDER + BEACON_VIDEO_API_KEY to render real video.",
                    "prompt": prompt}
        # --- INTEGRATION SEAM: call the chosen video API here ------------
        return {"kind": "video", "status": "provider_seam_not_implemented",
                "provider": VIDEO_PROVIDER, "prompt": prompt}

    def handle(self, req: AgentRequest) -> AgentResponse:
        prompt = str(req.payload.get("prompt", "")).strip()
        media_type = str(req.payload.get("media_type", "image")).lower()
        jurisdiction = req.payload.get("jurisdiction", "US")
        count = max(1, min(4, int(req.payload.get("count", 1) or 1)))
        if not prompt:
            return AgentResponse(False, blocked_reason="a creative prompt is required")

        ref_bytes, ref_mime = (None, None)
        ref_url = req.payload.get("reference_image", "")
        if ref_url:
            ref_bytes, ref_mime = _decode_data_url(str(ref_url))
            if ref_bytes is None:
                return AgentResponse(False, blocked_reason="reference_image must be a base64 image data URL")
            if len(ref_bytes) > 8_000_000:
                return AgentResponse(False, blocked_reason="reference image too large (max ~8 MB)")

        assets = []
        if media_type in ("image", "both"):
            for i in range(count):
                p = prompt if i == 0 else f"{prompt} (alternate composition #{i + 1})"
                # the uploaded photo guides every render in the batch
                assets.append(self._generate_image(p, ref_bytes, ref_mime))
        if media_type in ("video", "both"):
            assets.append(self._generate_video(prompt))

        live = any(a.get("status") == "rendered" for a in assets)
        notes = ["rendered media must be client-accepted before publish",
                 "compliance metadata retained server-side"]
        if not live:
            notes.append("running in stub mode — connect a generation provider to render real media")

        return AgentResponse(True,
                             data={"assets": assets, "ai_disclosure": True,
                                   "media_type": media_type, "count": count,
                                   "jurisdiction": jurisdiction},
                             gate_required="client", notes=notes)
