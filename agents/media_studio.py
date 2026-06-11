"""media_studio.py — TIER 3. Turns an approved creative brief into REAL
rendered media by calling a best-in-class generation model (image or video)
through its API. It does not train a model — it is the governed wrapper around
a rented one: Sentinel screens the brief, output is client-gated, and an
AI-disclosure flag rides along.

Provider wiring is a seam, configured by env so no key = honest stub mode:
  BEACON_IMAGE_PROVIDER  + BEACON_IMAGE_API_KEY   (e.g. "gemini" / "openai")
  BEACON_VIDEO_PROVIDER  + BEACON_VIDEO_API_KEY   (e.g. "veo" / "runway")
Flip a provider on by setting its env vars and implementing the one marked
call below — the rest of the mesh (routing, gating, screening) is unchanged.
"""
from __future__ import annotations
import os, base64
from base import Agent
from contracts import Capability, AgentRequest, AgentResponse

IMAGE_PROVIDER = os.environ.get("BEACON_IMAGE_PROVIDER", "").strip().lower()
VIDEO_PROVIDER = os.environ.get("BEACON_VIDEO_PROVIDER", "").strip().lower()
IMAGE_API_KEY = (os.environ.get("BEACON_IMAGE_API_KEY")
                 or os.environ.get("GEMINI_API_KEY") or "").strip()
GEMINI_IMAGE_MODEL = os.environ.get("BEACON_GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")


class MediaStudio(Agent):
    name, tier = "MediaStudio", 3
    serves = {Capability.GENERATE_MEDIA}

    # --- generation seams -----------------------------------------------
    def _generate_image(self, prompt: str) -> dict:
        if not IMAGE_PROVIDER:
            return {"kind": "image", "status": "no_provider_configured",
                    "note": "Set BEACON_IMAGE_PROVIDER + BEACON_IMAGE_API_KEY to render real images.",
                    "prompt": prompt}
        if IMAGE_PROVIDER == "gemini":
            return self._gemini_image(prompt)
        return {"kind": "image", "status": "unknown_provider",
                "provider": IMAGE_PROVIDER, "prompt": prompt}

    def _gemini_image(self, prompt: str) -> dict:
        """Render with Google Gemini ('Nano Banana'). Returns an inline data URL."""
        if not IMAGE_API_KEY:
            return {"kind": "image", "status": "no_api_key",
                    "note": "Set BEACON_IMAGE_API_KEY to your Google AI Studio key.", "prompt": prompt}
        try:
            from google import genai
            client = genai.Client(api_key=IMAGE_API_KEY)
            resp = client.models.generate_content(model=GEMINI_IMAGE_MODEL, contents=prompt)
            for part in resp.parts:
                if getattr(part, "inline_data", None) is not None:
                    raw = part.inline_data.data
                    mime = getattr(part.inline_data, "mime_type", "image/png")
                    b64 = base64.b64encode(raw).decode() if isinstance(raw, (bytes, bytearray)) else raw
                    return {"kind": "image", "status": "rendered", "provider": "gemini",
                            "model": GEMINI_IMAGE_MODEL, "data_url": f"data:{mime};base64,{b64}",
                            "prompt": prompt}
            return {"kind": "image", "status": "no_image_returned",
                    "note": "Model returned no image (it may have refused the prompt).", "prompt": prompt}
        except Exception as e:  # never crash the mesh on a provider hiccup
            return {"kind": "image", "status": "error", "provider": "gemini",
                    "note": f"{type(e).__name__}: {e}", "prompt": prompt}

    def _generate_video(self, prompt: str) -> dict:
        if not VIDEO_PROVIDER:
            return {"kind": "video", "status": "no_provider_configured",
                    "note": "Set BEACON_VIDEO_PROVIDER + BEACON_VIDEO_API_KEY to render real video.",
                    "prompt": prompt}
        # --- INTEGRATION SEAM: call the chosen video API here ------------
        # e.g. Veo, Runway, Kling. These are async — poll then return a URL.
        return {"kind": "video", "status": "provider_seam_not_implemented",
                "provider": VIDEO_PROVIDER, "prompt": prompt}

    def handle(self, req: AgentRequest) -> AgentResponse:
        prompt = str(req.payload.get("prompt", "")).strip()
        media_type = str(req.payload.get("media_type", "image")).lower()
        jurisdiction = req.payload.get("jurisdiction", "US")
        if not prompt:
            return AgentResponse(False, blocked_reason="a creative prompt is required")

        assets = []
        if media_type in ("image", "both"):
            assets.append(self._generate_image(prompt))
        if media_type in ("video", "both"):
            assets.append(self._generate_video(prompt))

        live = any(a.get("status", "").startswith("rendered") for a in assets)
        notes = ["rendered media must be client-accepted before publish",
                 "AI-disclosure required where the registry mandates it"]
        if not live:
            notes.append("running in stub mode — connect a generation provider to render real media")

        return AgentResponse(True,
                             data={"assets": assets, "ai_disclosure": True,
                                   "media_type": media_type, "jurisdiction": jurisdiction},
                             gate_required="client", notes=notes)
