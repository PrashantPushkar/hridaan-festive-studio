"""Indian festival greeting generator for Hridaan Labs's Festivities feature.

Two independent pieces:
- A short OpenAI-written WhatsApp greeting message plus a subtle one-line
  image tagline (structured output, same pattern as ai_client.py).
- A greeting image: an OpenAI GPT-image illustration for the scene (falls
  back to a local Pillow gradient/motif if no key or the call fails), sized
  per channel (see FORMAT_SPECS - Instagram Feed/WhatsApp chat at 4:5,
  Instagram Stories/WhatsApp Status at 9:16). The prompt sent to OpenAI is
  produced by a structured OpenAI "blueprint" (concept/composition/
  typography/logo placement - see run_greeting_blueprint_agent), which also
  tells the local compositing pass WHERE to draw the greeting text and Hridaan Labs
  logo - directly on the artwork's own negative space with only a soft,
  edgeless scrim behind for contrast, never an opaque card/banner (Sep 2026
  art-direction spec). Branding and text stay crisp regardless of which
  background was used.
"""
import asyncio
import base64
import json
import logging
import math
import os
import random
import hashlib
import urllib.error
import urllib.request
from io import BytesIO
from typing import Optional

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageStat

from . import ai_client as agents
from . import costs

logger = logging.getLogger(__name__)

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(ROOT_DIR, "assets", "fonts")

IMAGE_SIZE = 1080  # legacy square fallback, kept for any caller not specifying a channel

# -----------------------------------------------------------------------
# Per-channel output formats (Sep 2026 art-direction spec: 1:1 square is no
# longer the default - each channel gets its own native aspect ratio,
# generated directly rather than cropped from another ratio afterward).
# openai_size is the closest size gpt-image-1/2 natively support
# ("1024x1024" | "1024x1536" | "1536x1024"); we request the tallest native
# portrait option for both portrait targets, then centre-crop to the exact
# pixel dimensions in _cover_resize so neither format is a stretched crop of
# the other.
# -----------------------------------------------------------------------
FORMAT_SPECS = {
    "instagram_feed": {"ratio": "4:5", "width": 1080, "height": 1350, "openai_size": "1024x1536"},
    "instagram_stories": {"ratio": "9:16", "width": 1080, "height": 1920, "openai_size": "1024x1536"},
    "whatsapp_status": {"ratio": "9:16", "width": 1080, "height": 1920, "openai_size": "1024x1536"},
    "whatsapp_chat": {"ratio": "4:5", "width": 1080, "height": 1350, "openai_size": "1024x1536"},
}
DEFAULT_CHANNEL = "instagram_feed"


def _cover_resize(img: "Image.Image", target_w: int, target_h: int) -> "Image.Image":
    """Scale img up/down to cover a target_w x target_h box, then centre-crop
    the overflow - a standard 'cover' fit, so a portrait AI image can supply
    any of our channel aspect ratios without ever stretching or letterboxing."""
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = round(src_w * scale), round(src_h * scale)
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))

# -----------------------------------------------------------------------
# AI-generated illustrated background (optional). If OPENAI_API_KEY is set,
# each greeting gets a real illustrated festive scene from OpenAI's GPT
# image models instead of the geometric gradient+motif fallback below. The
# greeting card and Hridaan Labs logo pill are still composited locally on top
# either way, so branding and text stay crisp regardless of which
# background was used. Tries gpt-image-2 (current flagship - reasons about
# the prompt before generating, best text/detail accuracy) first, then
# falls back to gpt-image-1 if that model isn't available on the account.
# DALL-E 2/3 are not used: OpenAI shut them down on 2026-05-12. Fully
# optional and fails soft: any error (no key, quota, network, content
# policy, model unavailable) falls back to the deterministic Pillow
# background.
# -----------------------------------------------------------------------
_AI_MODELS_IN_PREFERENCE_ORDER = [os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-2")]

# Several scene variants per motif (rather than one fixed scene) so festivals
# that share a motif - "bloom" alone covers 16 of the 38 festivals - don't all
# render near-identical art. A festival's own rng (seeded by its name, see
# render_greeting_image) deterministically picks one variant + one
# composition style, so the same festival looks the same across regenerates
# but the calendar as a whole stays visually varied.
_AI_SCENE_VARIANTS = {
    "diya": [
        "rows of glowing terracotta oil lamps (diyas) with warm golden flames, scattered "
        "marigold petals and gold confetti, soft bokeh light",
        "a festive doorway framed with diyas, marigold garlands and rangoli powder, warm "
        "brass and copper accents",
        "diyas floating on a dark reflective surface with marigold petals drifting around "
        "them, warm fairy-light bokeh",
    ],
    "confetti": [
        "a joyful burst of colourful confetti, paper streamers and balloons in the air",
        "a shower of coloured powder mixing mid-air into a rainbow burst, playful energy",
        "festive paper lanterns, streamers and sparklers against a night sky",
    ],
    "crescent_star": [
        "a glowing golden crescent moon and star in a deep teal night sky, ornate geometric "
        "lantern patterns",
        "an arabesque-patterned arch with hanging lanterns, a crescent moon and twinkling "
        "stars",
        "a skyline silhouette under a crescent moon with strings of lanterns, jewel-toned sky",
    ],
    "rangoli": [
        "an elaborate, colourful rangoli flower pattern on the ground with marigolds and "
        "diyas",
        "a pookalam-style flower carpet arranged in concentric rings of many colours",
        "a rangoli powder pattern surrounded by scattered flower petals and small brass lamps",
    ],
    "tricolor": [
        "the Indian tricolour as flowing fabric or bunting, with the Ashoka Chakra motif, "
        "patriotic mood",
        "kites and bunting in saffron, white and green against a bright open sky, patriotic "
        "mood",
        "marigold garlands in saffron and green with a subtle Ashoka Chakra emblem, "
        "patriotic mood",
    ],
    "bloom": [
        "a cluster of blooming lotus flowers and marigolds in warm gold and coral light",
        "hibiscus and jasmine blossoms with a peacock feather motif, jewel tones of teal, "
        "magenta and gold",
        "banana leaves, marigold garlands and small brass diyas arranged like a festive "
        "doorway",
        "trailing jasmine vines, lotus blooms and oil lamps, dusk-toned pinks and ambers",
        "a bouquet of marigolds, roses and mango leaves tied with red thread, warm festive "
        "light",
        "a peacock feather fan, lotus blossoms and gold-leaf detailing, rich jewel tones",
    ],
    "snow": [
        "gentle falling snow, twinkling fairy lights and pine branches, a cosy winter "
        "evening mood",
        "a string of warm fairy lights wound through holly and pine, soft snowfall",
        "ornaments, ribbon and pine branches on a deep evergreen background with soft snow",
    ],
}

_COMPOSITION_STYLES = [
    "asymmetric composition with the artwork flowing in from one side, leaving open, "
    "airy space elsewhere",
    "decorative elements arranged like a border or frame around the edges of the canvas",
    "artwork scattered gently across the whole frame in a balanced, airy arrangement",
    "artwork concentrated along a diagonal sweep from one corner toward the opposite side",
]


def _ai_background_prompt(festival: dict, rng: random.Random) -> str:
    variants = _AI_SCENE_VARIANTS.get(festival["motif"], _AI_SCENE_VARIANTS["bloom"])
    scene = rng.choice(variants)
    composition = rng.choice(_COMPOSITION_STYLES)
    hexes = ", ".join(festival["palette"])
    return (
        f"A premium, editorial-quality flat-illustration greeting-card background "
        f"celebrating {festival['name']}, an Indian festival, for a creative studio's "
        f"official audience of students, faculty, friends and partners - warm "
        f"and joyful but polished and professional, never childish or generic clip-art. "
        f"Scene: {scene}. Composition: {composition}. Rich, varied colour palette built "
        f"around these tones: {hexes}, plus complementary accents - avoid a flat "
        f"single-hue wash; use at least 4-5 distinct hues with good contrast between "
        f"them. Include several different decorative props and objects, not just one "
        f"repeated element, rendered in a clean modern flat-illustration / vector-art "
        f"style with rich gradients and soft depth and light, no photorealism. Square "
        f"composition, gallery-worthy finish. Absolutely no text, no words, no letters, "
        f"no logos, no watermarks, no human faces in close-up."
    )


# -----------------------------------------------------------------------
# OpenAI agent - turns a simple festival brief into a production-ready
# image-generation prompt, instead of building the prompt from a fixed
# local template. The template above (_ai_background_prompt) is kept as
# the fallback when this call is unavailable or fails for any reason.
# -----------------------------------------------------------------------
IMAGE_PROMPT_SYSTEM = """You are an expert creative director specialising in premium festive greeting designs for brands and organisations.

Your job is NOT to generate the image.

Your job is to transform a user's simple festival request into one highly detailed, production-ready image-generation prompt that will be passed to an image-generation model.

The final image should look professionally art-directed, premium, culturally appropriate and suitable for posting by a modern organisation on social media.

1. UNDERSTAND THE OCCASION

Identify the festival, celebration, national observance or special occasion requested by the user.

Understand its cultural context, traditional symbolism, colours, objects, decorations, atmosphere and commonly associated visual language.

Examples include, but are not limited to:

Diwali
Holi
Dussehra
Navratri
Durga Puja
Ganesh Chaturthi
Janmashtami
Raksha Bandhan
Onam
Pongal
Makar Sankranti
Lohri
Baisakhi
Ugadi
Gudi Padwa
Eid
Ramadan
Christmas
Easter
Guru Nanak Jayanti
Mahavir Jayanti
Buddha Purnima
Gandhi Jayanti
Independence Day
Republic Day
Teachers' Day
Children's Day
New Year
Women's Day
Engineers' Day
Environment Day
and other regional, national and international occasions.

Never mechanically apply the same visual template to every festival.

2. DEVELOP A VISUAL CONCEPT

Create one clear central visual idea appropriate to the occasion.

Select culturally meaningful objects, patterns, architecture, nature, lighting or decorative elements.

For example:

Diwali -> diyas, warm golden illumination, rangoli, flowers and elegant festive interiors.

Holi -> organic clouds of gulal, energetic movement and vibrant spring colours.

Eid -> crescent moon, lanterns, elegant Islamic geometric patterns and atmospheric evening light.

Christmas -> warm festive lights, tasteful greenery, ornaments and winter-inspired details.

Onam -> pookalam, Kerala-inspired visual elements, flowers and warm natural colours.

Independence Day -> elegant saffron, white and green visual language with appropriate national symbolism.

Do not use religious or cultural elements merely as decoration when doing so would be disrespectful or inaccurate.

3. PREMIUM ART DIRECTION

The result should feel like work created by a professional advertising agency rather than a generic greeting-card template.

Specify:

- composition
- foreground and background
- focal point
- lighting
- depth
- textures
- materials
- atmosphere
- colour palette
- decorative details
- negative space
- typography placement
- visual hierarchy

Prefer sophisticated, contemporary and elegant compositions.

Avoid overcrowding.

Use realistic materials, subtle textures, beautiful lighting and carefully controlled detail.

4. TYPOGRAPHY

Greeting text must be highly legible.

Create a clear hierarchy:

Festival greeting -> largest
Supporting message -> smaller
Date, if relevant -> secondary
Brand area -> subtle

Use appropriate typography such as refined serif, modern sans-serif, elegant Indian-inspired display typography or tasteful calligraphic styling depending on the occasion.

Never generate unnecessary text.

Never invent quotes and attribute them to historical, religious or public figures.

If the user provides exact wording, preserve it exactly.

If the user provides no greeting text, create a short, tasteful greeting appropriate to the occasion.

5. BRAND-SAFE DESIGN

The image should be appropriate for a professional company, educational organisation, startup or institution.

Avoid:

cheap clip-art appearance
overly saturated colours
excessive decorative elements
crowded layouts
random symbols
watermarks
fake logos
unrequested brand names
unnecessary text
distorted objects
poor typography
political party imagery

Unless explicitly requested, do not portray identifiable real people.

For commemorative occasions involving historical figures, symbolic representation may be used when appropriate.

6. SOCIAL MEDIA FORMAT

If the user specifies a platform or aspect ratio, optimise the composition accordingly.

Otherwise default to:

Square 1:1 social-media post.

Keep important content away from edges.

Ensure the greeting remains readable on a mobile screen.

7. VISUAL QUALITY

Aim for:

premium advertising campaign quality
editorial art direction
beautiful realistic lighting
refined colour grading
high-detail materials
balanced composition
professional typography
clean edges
subtle depth
high-resolution appearance
social-media-ready finish

The design should feel intentional and handcrafted rather than automatically generated.

8. VARIETY

Do not generate the same composition repeatedly.

Vary intelligently between:

editorial photography
premium 3D illustration
luxury graphic design
paper-art compositions
traditional Indian craft-inspired artwork
watercolour
modern minimalism
cinematic still-life
architectural compositions
botanical compositions
tasteful mixed-media artwork

Choose the style that best suits the festival.

9. INTEGRATION CONSTRAINT (overrides section 4 above for this pipeline)

This particular prompt is for the ILLUSTRATED BACKGROUND ONLY. The greeting
headline, tagline, subline and the organisation's logo are composited
afterward by a separate, precise local rendering pass - NOT by the image
model. Because of this:

- The image you describe must contain ZERO text, words, letters, numerals,
  quotes, logos or watermarks of any kind. Do not render the greeting text
  into the scene, even though section 4 above describes how such text
  would normally be hierarchically composed - instead, treat that
  hierarchy as a guide for WHERE to leave clean, uncluttered negative
  space (e.g. a calm area for a headline, a corner for a logo), not as an
  instruction to draw the text or logo itself.
- Still honour everything else above: the visual concept, art direction,
  premium quality, brand safety, format and variety guidance all apply in
  full to the background scene itself.

OUTPUT RULE

Return ONLY the final image-generation prompt.

Do not explain your choices.
Do not provide headings.
Do not provide alternatives.
Do not mention these instructions.
Do not output JSON unless explicitly requested.

The output must be ready to send directly to the image-generation model."""


async def run_image_prompt_agent(festival: dict, greeting: Optional[dict] = None) -> Optional[str]:
    """Turns this festival's brief into a production-ready image-generation
    prompt via OpenAI (see IMAGE_PROMPT_SYSTEM), returning the prompt text
    verbatim. Returns None on any failure (no API key, network, bad
    response) so the caller falls back to the local template prompt -
    generating a background is best-effort, this refinement step doubly so.
    """
    try:
        client = agents.get_client()
    except Exception as e:
        logger.warning(f"[IMAGE PROMPT] client unavailable: {e}")
        return None

    greeting_text = (greeting or {}).get("tagline") or (greeting or {}).get("whatsapp_message") or "Happy " + festival["name"]
    user = (
        f"Festival: {festival['name']}\n"
        f"What it marks: {festival['blurb']}\n"
        f"Category: {festival['category']}\n"
        f"Platform: WhatsApp broadcast image + social media post\n"
        f"Aspect ratio: square 1:1 (must read clearly on mobile screens)\n"
        f"Language: English\n"
        f"Greeting text (for mood/context only - see integration constraint, "
        f"do not render this text into the image): {greeting_text}\n"
        f"Brand: Hridaan Labs, a creative brand addressing customers, colleagues, "
        f"friends and partners\n"
        f"Brand personality: human-centred, warm, premium, contemporary, never "
        f"childish or generic clip-art\n"
        f"Logo placement: reserve calm negative space in one corner (exact corner "
        f"decided separately, do not depict a logo)\n"
        f"Creative freedom: high - choose the visual concept and art style "
        f"yourself per the brief\n"
        f"Curated colour palette to favour as a starting point (not mandatory): "
        f"{', '.join(festival['palette'])}"
    )

    try:
        response = await agents._create(
            client, system=IMAGE_PROMPT_SYSTEM, user=user, schema=None, max_tokens=1500,
        )
        prompt = agents._first_text(response).strip()
        return prompt or None
    except Exception as e:
        logger.warning(f"[IMAGE PROMPT] OpenAI call failed: {e}")
        return None


# -----------------------------------------------------------------------
# OpenAI agent - structured creative "blueprint" (Sep 2026 art-direction
# spec, Section 10). Rather than one opaque prose prompt, this call returns
# a small structured JSON object covering concept, composition, typography
# and logo placement - so the calling application (not just the wording of
# a prompt) owns and can validate the plan: build_prompt_from_blueprint()
# then assembles the actual image-model prompt from it deterministically,
# and render_greeting_image() reads the same blueprint to decide WHERE to
# draw the locally-composited greeting text and logo, so the two stay in
# sync instead of the text always landing in the same fixed spot.
#
# This is additive, not a replacement: if this call is unavailable or
# fails, generate_ai_background() falls back to run_image_prompt_agent's
# free-prose prompt, then to the local _ai_background_prompt template - the
# same graceful degradation as before.
# -----------------------------------------------------------------------
BLUEPRINT_SYSTEM = """You are the creative director for Hridaan Labs's festive greeting artwork - a creative \
studio's greetings to customers, colleagues, friends and partners across Instagram and WhatsApp.

Given a festival/occasion brief, greeting copy and target channel, produce ONE structured creative blueprint \
(not prose) for a premium, editorial, art-directed greeting visual.

CORE PRINCIPLES

- One clear central visual concept per occasion, culturally accurate and never a generic template mechanically \
reapplied across festivals (diyas and rangoli for Diwali; gulal clouds for Holi; crescent moon and lanterns for \
Eid; pookalam for Onam; saffron/white/green with restrained national symbolism for Independence Day; and so on \
for any other festival, observance or occasion named in the brief).
- Represent the occasion respectfully. Do not force technology, education or business symbols into the artwork.
- Visual-first composition: the artwork/hero subject is 65-75% of the attention, greeting typography 20-30%, \
branding 5-10%. The hero subject is never obscured by text.
- NEVER plan a large text container: no white/opaque card, banner, floating panel, oversized rounded rectangle \
or lower-third strip. Typography sits directly in genuine negative space the composition was designed to leave \
open - describe WHERE that quiet space is (e.g. "left 35-40% of frame", "upper area above the hero", "lower-left \
third"), not a box to draw there.
- Place the hero subject off-centre when it helps the typography breathe (e.g. hero right 55-60% / greeting left \
35-40%, or hero upper-middle / greeting in the naturally clean lower area).
- Never place important text across a face, religious icon, or culturally significant detail.
- Cinematic depth: foreground detail, hero subject, atmospheric background - realistic light, shadow, texture, \
material, sophisticated colour grading. Avoid generic stock-photo or template (Canva-style) looks, excessive \
symmetry, clip-art, or oversized headline text.
- Vary style choice intelligently across festivals - editorial photography, premium 3D illustration, luxury \
graphic design, paper-art, traditional Indian craft-inspired art, watercolour, modern minimalism, cinematic \
still-life, architectural or botanical composition, tasteful mixed media - pick whichever best suits this \
specific occasion, not the same style every time.
- Brand-safe: no cheap clip-art, no crowded layout, no watermark, no fake/extra logos, no unrequested brand \
names, no distorted objects, no political-party imagery. Do not portray identifiable real people unless asked; \
symbolic representation is fine for commemorative occasions involving historical figures.
- The greeting text height must stay within max_height_pct of total canvas height, 20 at the absolute most - \
prefer 15-18.

Return only the blueprint fields defined by the schema - no prose commentary outside them."""

BLUEPRINT_SCHEMA = {
    "type": "object",
    "properties": {
        "concept": {
            "type": "object",
            "properties": {
                "theme": {"type": "string"},
                "mood": {"type": "array", "items": {"type": "string"}},
                "cultural_notes": {"type": "string"},
                "art_style": {"type": "string"},
            },
            "required": ["theme", "mood", "cultural_notes", "art_style"],
            "additionalProperties": False,
        },
        "composition": {
            "type": "object",
            "properties": {
                "hero_subject": {
                    "type": "object",
                    "properties": {"description": {"type": "string"}, "position": {"type": "string"}},
                    "required": ["description", "position"], "additionalProperties": False,
                },
                "secondary_elements": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"description": {"type": "string"}, "position": {"type": "string"}},
                        "required": ["description", "position"], "additionalProperties": False,
                    },
                },
                "atmosphere": {
                    "type": "object",
                    "properties": {"background": {"type": "string"}, "lighting": {"type": "string"}},
                    "required": ["background", "lighting"], "additionalProperties": False,
                },
                "negative_space": {"type": "array", "items": {"type": "string"}},
                "depth_layers": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["hero_subject", "secondary_elements", "atmosphere", "negative_space", "depth_layers"],
            "additionalProperties": False,
        },
        "typography": {
            "type": "object",
            "properties": {
                "greeting": {
                    "type": "object",
                    "properties": {
                        "placement": {"type": "string"},
                        "treatment": {"type": "string"},
                        "max_height_pct": {"type": "number"},
                    },
                    "required": ["placement", "treatment", "max_height_pct"], "additionalProperties": False,
                },
                "supporting_line": {
                    "type": "object",
                    "properties": {"placement": {"type": "string"}, "treatment": {"type": "string"}},
                    "required": ["placement", "treatment"], "additionalProperties": False,
                },
            },
            "required": ["greeting", "supporting_line"],
            "additionalProperties": False,
        },
        "logo_safe_zone": {
            "type": "object",
            "properties": {"position": {"type": "string"}, "clear_space_pct": {"type": "number"}},
            "required": ["position", "clear_space_pct"], "additionalProperties": False,
        },
        "strict_exclusions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["concept", "composition", "typography", "logo_safe_zone", "strict_exclusions"],
    "additionalProperties": False,
}


def _validate_blueprint(blueprint: dict) -> dict:
    """Lightweight lint pass, not a full geometry checker (positions are
    free-text descriptions, not coordinates) - clamps the one field we can
    meaningfully enforce numerically, and fills safe defaults for anything
    unexpectedly missing so a slightly malformed response still degrades
    gracefully instead of raising deep in the render path."""
    typography = blueprint.setdefault("typography", {})
    greeting = typography.setdefault("greeting", {})
    try:
        greeting["max_height_pct"] = min(float(greeting.get("max_height_pct", 18)), 20.0)
    except (TypeError, ValueError):
        greeting["max_height_pct"] = 18.0
    greeting.setdefault("placement", "lower-left third")
    typography.setdefault("supporting_line", {"placement": "beneath greeting", "treatment": "small sans-serif"})
    blueprint.setdefault("logo_safe_zone", {"position": "upper-left corner", "clear_space_pct": 8})
    blueprint.setdefault("strict_exclusions", [])
    blueprint["strict_exclusions"] = list(blueprint["strict_exclusions"]) + [
        "no white or opaque text card", "no banner", "no floating panel", "no oversized typography",
    ]
    return blueprint


async def run_greeting_blueprint_agent(festival: dict, greeting: Optional[dict] = None,
                                        channel: str = DEFAULT_CHANNEL) -> Optional[dict]:
    """Returns a validated creative blueprint dict (see BLUEPRINT_SCHEMA), or
    None on any failure - caller falls back to the older free-prose prompt
    agent when this isn't available."""
    try:
        client = agents.get_client()
    except Exception as e:
        logger.warning(f"[BLUEPRINT] client unavailable: {e}")
        return None

    fmt = FORMAT_SPECS.get(channel, FORMAT_SPECS[DEFAULT_CHANNEL])
    greeting_text = (greeting or {}).get("tagline") or (greeting or {}).get("whatsapp_message") or f"Happy {festival['name']}"
    user = (
        f"Festival / occasion: {festival['name']}\n"
        f"What it marks: {festival['blurb']}\n"
        f"Category: {festival['category']}\n"
        f"Channel: {channel} ({fmt['ratio']} aspect ratio, {fmt['width']}x{fmt['height']}px)\n"
        f"Language: English\n"
        f"Greeting text to plan space for (composite locally afterward - do not draw it into the image "
        f"yourself, this call only plans WHERE it goes): {greeting_text}\n"
        f"Brand: Hridaan Labs, a creative studio\n"
        f"Brand personality: human-centred AI, innovative, warm, premium\n"
        f"Creative freedom: high - choose the concept and art style yourself per the brief\n"
        f"Curated colour palette to favour as a starting point (not mandatory): {', '.join(festival['palette'])}"
    )
    try:
        response = await agents._create(
            client, system=BLUEPRINT_SYSTEM, user=user, schema=BLUEPRINT_SCHEMA, max_tokens=2000,
        )
        blueprint = agents.extract_json_object(agents._first_text(response))
        return _validate_blueprint(blueprint)
    except Exception as e:
        logger.warning(f"[BLUEPRINT] OpenAI call failed: {e}")
        return None


def build_prompt_from_blueprint(festival: dict, blueprint: dict, channel: str = DEFAULT_CHANNEL) -> str:
    """Deterministic template - no free-writing - that assembles the actual
    image-generation prompt from a validated blueprint's fields."""
    fmt = FORMAT_SPECS.get(channel, FORMAT_SPECS[DEFAULT_CHANNEL])
    concept = blueprint.get("concept", {})
    comp = blueprint.get("composition", {})
    hero = comp.get("hero_subject", {})
    secondary = comp.get("secondary_elements", [])
    atmosphere = comp.get("atmosphere", {})
    negative_space = comp.get("negative_space", [])
    depth_layers = comp.get("depth_layers", [])
    exclusions = blueprint.get("strict_exclusions", [])

    secondary_txt = "; ".join(f"{e.get('description', '')} ({e.get('position', '')})" for e in secondary) or "none"
    parts = [
        f"A premium, editorial-quality {concept.get('art_style', 'illustration')} celebrating "
        f"{festival['name']} for Hridaan Labs, a creative brand addressing customers, colleagues, parents and "
        f"corporate partners - {', '.join(concept.get('mood', []) or ['warm', 'premium'])} in mood, sophisticated "
        f"and never generic clip-art or a stock-photo template.",
        f"Central concept: {concept.get('theme', festival['blurb'])}.",
    ]
    if concept.get("cultural_notes"):
        parts.append(f"Cultural context: {concept['cultural_notes']}.")
    parts.append(f"Hero subject: {hero.get('description', festival['name'])}, positioned at {hero.get('position', 'centre')}.")
    parts.append(f"Secondary elements: {secondary_txt}.")
    parts.append(f"Atmosphere/background: {atmosphere.get('background', 'a softly lit festive scene')}. "
                 f"Lighting: {atmosphere.get('lighting', 'warm, directional, cinematic')}.")
    if depth_layers:
        parts.append(f"Depth, front to back: {' -> '.join(depth_layers)}.")
    parts.append(
        f"Reserve genuinely clean, uncluttered negative space at: {', '.join(negative_space) or 'one clear region of the frame'} "
        f"- no object, texture or busy detail should cross into these areas, they exist so text can be placed there "
        f"afterward without a background box."
    )
    parts.append(
        f"Colour palette to favour: {', '.join(festival['palette'])}, plus complementary accents, at least 4-5 "
        f"distinct hues with good contrast between them, no flat single-hue wash."
    )
    parts.append(
        f"{fmt['ratio']} aspect ratio composition ({fmt['width']}x{fmt['height']}px), important content kept away "
        f"from the edges, must read clearly on a mobile screen."
    )
    parts.append(
        "Premium advertising-campaign quality, editorial art direction, realistic lighting and material texture, "
        "refined colour grading, balanced composition, clean edges, high-resolution finish - intentional and "
        "handcrafted, not automatically generated."
    )
    parts.append(
        "STRICT: absolutely no text, no words, no letters, no numerals, no logos, no watermarks, no human faces "
        "in close-up - the greeting text and logo are composited separately afterward, this image is the "
        "illustrated background only. " + "; ".join(exclusions) + "."
    )
    return " ".join(parts)


def _call_openai_image_api(prompt: str, size: str = "1024x1024") -> Optional[bytes]:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return None
    url = "https://api.openai.com/v1/images/generations"
    for model in _AI_MODELS_IN_PREFERENCE_ORDER:
        payload = json.dumps({
            "model": model,
            "prompt": agents.personalize(prompt),
            "n": 1,
            "size": size,
            "quality": os.environ.get("OPENAI_IMAGE_QUALITY", "medium"),
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload, method="POST",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        entry = costs.start_call(model, 'image')
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            costs.finish_call(entry, data)
            return base64.b64decode(data["data"][0]["b64_json"])
        except urllib.error.HTTPError as e:
            logger.warning("Image generation HTTP status %s", e.code)
            continue
        except Exception as e:
            logger.warning(f"[AI BACKGROUND] {model} call failed: {e}")
            continue
    return None


async def generate_ai_background(festival: dict, seed: Optional[int] = None, greeting: Optional[dict] = None,
                                  channel: str = DEFAULT_CHANNEL, use_text_ai: bool = True) -> tuple:
    """Best-effort: returns (image, blueprint) for this festival at the given
    channel's native aspect ratio, or (None, None) if OPENAI_API_KEY isn't set
    or every tier below fails. blueprint is the structured creative plan used
    to build the prompt (or None if only the older/local prompt tiers ran) -
    render_greeting_image reads it to decide where to draw text/logo so the
    two stay in sync.

    Three-tier prompt fallback, each best-effort and independent of the last:
      1. run_greeting_blueprint_agent + build_prompt_from_blueprint - structured
         OpenAI blueprint (concept/composition/typography/logo), deterministically
         templated into the final prompt (Sep 2026 art-direction spec).
      2. run_image_prompt_agent - OpenAI free-prose prompt (previous approach).
      3. _ai_background_prompt - local fixed template, no network call.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        return None, None
    fmt = FORMAT_SPECS.get(channel, FORMAT_SPECS[DEFAULT_CHANNEL])
    rng = random.Random(seed if seed is not None else int.from_bytes(hashlib.sha256(festival["name"].encode()).digest()[:4], "big") % 10_000)

    if use_text_ai:
        blueprint = await run_greeting_blueprint_agent(festival, greeting, channel)
        if blueprint:
            prompt = build_prompt_from_blueprint(festival, blueprint, channel)
        else:
            prompt = await run_image_prompt_agent(festival, greeting)
            if not prompt:
                prompt = _ai_background_prompt(festival, rng)

    else:
        blueprint = None
        prompt = _ai_background_prompt(festival, rng)

    raw = await asyncio.to_thread(_call_openai_image_api, prompt, fmt["openai_size"])
    if not raw:
        return None, blueprint
    try:
        img = Image.open(BytesIO(raw)).convert("RGBA")
        return _cover_resize(img, fmt["width"], fmt["height"]), blueprint
    except Exception as e:
        logger.warning(f"[AI BACKGROUND] decode failed: {e}")
        return None, blueprint

# -----------------------------------------------------------------------
# Festival calendar (2026) - name, date, category, one-line blurb, and the
# visual treatment (palette + motif) used to render its greeting card.
# Dates researched against multiple 2026 Indian-calendar sources; lunar/
# moon-sighting-dependent festivals (Eid, Muharram) are best-estimate.
# -----------------------------------------------------------------------
FESTIVALS_2026 = [
    {"name": "New Year's Day", "date": "2026-01-01", "category": "national",
     "blurb": "The start of the calendar year.", "motif": "confetti",
     "palette": ["#1B1F3B", "#3A2E6E", "#8B5CF6", "#F2C14E"]},
    {"name": "Lohri", "date": "2026-01-13", "category": "hindu",
     "blurb": "North Indian bonfire harvest festival.", "motif": "rangoli",
     "palette": ["#7A2E0E", "#C4531D", "#E8901A", "#F2C14E"]},
    {"name": "Makar Sankranti / Pongal", "date": "2026-01-14", "category": "hindu",
     "blurb": "Harvest festival marking the sun's transit into Capricorn.", "motif": "rangoli",
     "palette": ["#8A4B0F", "#D97B1E", "#F2A93B", "#FFDA77"]},
    {"name": "Vasant Panchami", "date": "2026-01-23", "category": "hindu",
     "blurb": "Marks the onset of spring; dedicated to Goddess Saraswati.", "motif": "bloom",
     "palette": ["#5B4A00", "#A68B12", "#E2C34A", "#FFF1A6"]},
    {"name": "Republic Day", "date": "2026-01-26", "category": "national",
     "blurb": "India's Constitution came into effect on this day in 1950.", "motif": "tricolor",
     "palette": ["#1B4F9C", "#2E7BC4", "#F2F2F2", "#1B8A4A"]},
    {"name": "Maha Shivratri", "date": "2026-02-15", "category": "hindu",
     "blurb": "The 'Great Night of Shiva'.", "motif": "bloom",
     "palette": ["#0B1D4D", "#1E3A8A", "#5B7FDB", "#C7D2FE"]},
    {"name": "Holi", "date": "2026-03-04", "category": "hindu",
     "blurb": "The festival of colours.", "motif": "confetti",
     "palette": ["#3E1F6E", "#B4348C", "#F2545B", "#FFB93C"]},
    {"name": "Ram Navami", "date": "2026-03-26", "category": "hindu",
     "blurb": "Celebrates the birth of Lord Rama.", "motif": "bloom",
     "palette": ["#7A1E1E", "#C4451D", "#E8901A", "#FFDA77"]},
    {"name": "Eid al-Fitr", "date": "2026-03-30", "category": "muslim",
     "blurb": "Marks the end of Ramadan.", "motif": "crescent_star",
     "palette": ["#0B4A38", "#0E6B52", "#159873", "#2ABE93"]},
    {"name": "Mahavir Jayanti", "date": "2026-03-31", "category": "jain",
     "blurb": "Birth anniversary of Lord Mahavira, the 24th Tirthankara.", "motif": "bloom",
     "palette": ["#3A2E1A", "#7A6230", "#C9A94E", "#F4E7C1"]},
    {"name": "Good Friday", "date": "2026-04-03", "category": "christian",
     "blurb": "Commemorates the crucifixion of Jesus Christ.", "motif": "bloom",
     "palette": ["#1E1B3A", "#3B3170", "#6D5AA6", "#C4B8E0"]},
    {"name": "Easter Sunday", "date": "2026-04-05", "category": "christian",
     "blurb": "Celebrates the resurrection of Jesus Christ.", "motif": "bloom",
     "palette": ["#1E4D3A", "#3D8B6A", "#8FD4A8", "#FDF0C4"]},
    {"name": "Baisakhi", "date": "2026-04-14", "category": "sikh",
     "blurb": "Harvest festival and Sikh new year.", "motif": "rangoli",
     "palette": ["#7A4B00", "#D9821E", "#F2B33D", "#FFE58A"]},
    {"name": "Buddha Purnima", "date": "2026-05-01", "category": "buddhist",
     "blurb": "Marks the birth, enlightenment and death of Gautama Buddha.", "motif": "bloom",
     "palette": ["#6B3A00", "#B4791E", "#E8B93D", "#FFF1C9"]},
    {"name": "Eid al-Adha", "date": "2026-05-28", "category": "muslim",
     "blurb": "The 'Festival of Sacrifice'.", "motif": "crescent_star",
     "palette": ["#0B3D2E", "#0E5A45", "#137A5C", "#57C7A3"]},
    {"name": "Muharram", "date": "2026-06-26", "category": "muslim",
     "blurb": "The first month of the Islamic calendar; a day of remembrance.", "motif": "crescent_star",
     "palette": ["#111827", "#1F2937", "#374151", "#9CA3AF"]},
    {"name": "Rath Yatra", "date": "2026-07-16", "category": "hindu",
     "blurb": "The chariot procession of Lord Jagannath in Puri.", "motif": "bloom",
     "palette": ["#7A2E0E", "#C4531D", "#E8901A", "#FFDA77"]},
    {"name": "Guru Purnima", "date": "2026-07-29", "category": "hindu",
     "blurb": "A day to honour teachers and spiritual guides.", "motif": "bloom",
     "palette": ["#4A1E3A", "#8B3468", "#C45B94", "#F2B8D4"]},
    {"name": "Independence Day", "date": "2026-08-15", "category": "national",
     "blurb": "Marks India's independence from British rule in 1947.", "motif": "tricolor",
     "palette": ["#1B4F9C", "#2E7BC4", "#F2F2F2", "#1B8A4A"]},
    {"name": "Eid-e-Milad", "date": "2026-08-26", "category": "muslim",
     "blurb": "Commemorates the birth of Prophet Muhammad.", "motif": "crescent_star",
     "palette": ["#0B3D2E", "#0E5A45", "#137A5C", "#57C7A3"]},
    {"name": "Onam", "date": "2026-08-26", "category": "hindu",
     "blurb": "Kerala's harvest festival, famous for pookalam flower carpets.", "motif": "rangoli",
     "palette": ["#0F5C3A", "#1E8449", "#F4B400", "#E85D2C"]},
    {"name": "Raksha Bandhan", "date": "2026-08-28", "category": "hindu",
     "blurb": "Celebrates the bond between brothers and sisters.", "motif": "bloom",
     "palette": ["#7A1E2E", "#C4341D", "#E8901A", "#FFDA9C"]},
    {"name": "Janmashtami", "date": "2026-09-04", "category": "hindu",
     "blurb": "Celebrates the birth of Lord Krishna.", "motif": "bloom",
     "palette": ["#0B2E4A", "#1E4E8B", "#3E7FC7", "#F2C14E"]},
    {"name": "Teachers' Day", "date": "2026-09-05", "category": "national",
     "blurb": "Honours teachers, marked on Dr. Radhakrishnan's birthday.", "motif": "bloom",
     "palette": ["#0B3D5C", "#1E6B8B", "#3E9FBF", "#F2C14E"]},
    {"name": "Ganesh Chaturthi", "date": "2026-09-14", "category": "hindu",
     "blurb": "Celebrates the birth of Lord Ganesha.", "motif": "bloom",
     "palette": ["#7A1E1E", "#C4451D", "#E8901A", "#FFDA77"]},
    {"name": "Gandhi Jayanti", "date": "2026-10-02", "category": "national",
     "blurb": "Birth anniversary of Mahatma Gandhi.", "motif": "bloom",
     "palette": ["#3A3A3A", "#6B6B6B", "#E8901A", "#F5F0E1"]},
    {"name": "Sharad Navratri Begins", "date": "2026-10-11", "category": "hindu",
     "blurb": "Nine nights honouring the Goddess Durga.", "motif": "bloom",
     "palette": ["#4A0E3A", "#8B1E6B", "#C4459C", "#F2A9D4"]},
    {"name": "Dussehra", "date": "2026-10-20", "category": "hindu",
     "blurb": "Marks the triumph of good over evil; Vijayadashami.", "motif": "bloom",
     "palette": ["#7A1E1E", "#C4341D", "#E8901A", "#FFDA77"]},
    {"name": "Karva Chauth", "date": "2026-10-29", "category": "hindu",
     "blurb": "A fast observed until moonrise for a spouse's wellbeing.", "motif": "crescent_star",
     "palette": ["#3A0E1E", "#7A1E3A", "#B4345C", "#F2A93B"]},
    {"name": "Dhanteras", "date": "2026-11-06", "category": "hindu",
     "blurb": "Marks the start of the Diwali festivities.", "motif": "diya",
     "palette": ["#5A1424", "#B5342F", "#E07A2E", "#F2B33D"]},
    {"name": "Naraka Chaturdashi", "date": "2026-11-07", "category": "hindu",
     "blurb": "'Choti Diwali' - the eve of Diwali.", "motif": "diya",
     "palette": ["#5A1424", "#B5342F", "#E07A2E", "#F2B33D"]},
    {"name": "Diwali", "date": "2026-11-08", "category": "hindu",
     "blurb": "The festival of lights.", "motif": "diya",
     "palette": ["#5A1424", "#B5342F", "#E07A2E", "#F2B33D"]},
    {"name": "Govardhan Puja", "date": "2026-11-10", "category": "hindu",
     "blurb": "Celebrates Krishna lifting the Govardhan hill.", "motif": "diya",
     "palette": ["#0F5C3A", "#1E8449", "#E8901A", "#F2C14E"]},
    {"name": "Bhai Dooj", "date": "2026-11-11", "category": "hindu",
     "blurb": "Celebrates the bond between brothers and sisters.", "motif": "bloom",
     "palette": ["#7A1E2E", "#C4341D", "#E8901A", "#FFDA9C"]},
    {"name": "Children's Day", "date": "2026-11-14", "category": "national",
     "blurb": "Marked on Jawaharlal Nehru's birthday.", "motif": "confetti",
     "palette": ["#0B3D5C", "#1E8B8B", "#F2A93B", "#FF6B6B"]},
    {"name": "Chhath Puja", "date": "2026-11-15", "category": "hindu",
     "blurb": "A festival of gratitude to the Sun god.", "motif": "bloom",
     "palette": ["#7A2E0E", "#C4531D", "#E8901A", "#FFDA77"]},
    {"name": "Guru Nanak Jayanti", "date": "2026-11-24", "category": "sikh",
     "blurb": "Birth anniversary of Guru Nanak, founder of Sikhism.", "motif": "bloom",
     "palette": ["#0B2E5C", "#1E4E8B", "#E8901A", "#F2C14E"]},
    {"name": "Christmas", "date": "2026-12-25", "category": "christian",
     "blurb": "Celebrates the birth of Jesus Christ.", "motif": "snow",
     "palette": ["#0B2545", "#1B3B6F", "#8B1E3F", "#C0392B"]},
]


# Existing positions are persisted in SQLite. Keep original entries in place.
with open(os.path.join(ROOT_DIR, "festival_catalogue.json"), encoding="utf-8") as _catalogue_file:
    FESTIVALS_2026.extend(json.load(_catalogue_file)["additions"])
for _festival in FESTIVALS_2026:
    _festival.setdefault("aliases", [])
    _festival.setdefault("regions", ["India"])
    _festival.setdefault("source_urls", [])
    _festival.setdefault("date_note", "Legacy 2026 date; confirm against your regional calendar before scheduling.")
    if _festival["name"] == "Eid al-Fitr":
        _festival.update(date="2026-03-21", source_urls=["https://www.telangana.gov.in/downloads/calendar-2026/"], date_note="Telangana reference date; regional announcements and moon sighting may differ.")
    if _festival["name"] == "Makar Sankranti / Pongal":
        _festival["date_note"] = "Legacy combined option: 14 January for Makar Sankranti. Select the separate Pongal option for 15 January."
    if _festival["name"] == "Dussehra":
        _festival["aliases"] += ["Mysore Dasara", "Mysuru Dasara", "Vijayadashami"]
    if _festival["name"] == "Diwali":
        _festival["aliases"] += ["Deepavali"]


# Recheck inherited calendar entries before presenting them as planning references.
_CENTRAL_CALENDAR = "https://saiindia.gov.in/defence/new-delhi/en/page-defence-new-delhi-holidaylist"
_CENTRAL_VERIFIED = {"New Year's Day", "Republic Day", "Vasant Panchami", "Maha Shivratri", "Holi", "Ram Navami", "Mahavir Jayanti", "Good Friday", "Easter Sunday", "Buddha Purnima", "Independence Day", "Eid-e-Milad", "Janmashtami", "Gandhi Jayanti", "Dussehra", "Diwali", "Guru Nanak Jayanti", "Christmas", "Rath Yatra", "Onam", "Raksha Bandhan", "Ganesh Chaturthi", "Karva Chauth", "Bhai Dooj", "Chhath Puja"}
for _festival in FESTIVALS_2026[:38]:
    if _festival["name"] in _CENTRAL_VERIFIED:
        _festival.update(source_urls=[_CENTRAL_CALENDAR], date_note="2026 central government calendar reference; local observances can differ.")
    if _festival["name"] == "Muharram":
        _festival.update(source_urls=["https://www.telangana.gov.in/downloads/calendar-2026/"], date_note="26 June follows Telangana's calendar. Subject to local moon-sighting announcements.")
    if _festival["name"] == "Naraka Chaturdashi":
        _festival.update(date="2026-11-08",source_urls=[_CENTRAL_CALENDAR],date_note="8 November follows the central holiday list. Regional Choti Diwali observances can differ.")
    if _festival["name"] == "Govardhan Puja":
        _festival.update(date="2026-11-09",source_urls=[_CENTRAL_CALENDAR],date_note="9 November follows the central holiday list. Regional observances may use a different day.")
    if _festival["name"] == "Eid al-Adha":
        _festival.update(date="2026-05-27",source_urls=[_CENTRAL_CALENDAR],date_note="27 May central-calendar reference; some regional calendars list 28 May. Follow local moon-sighting announcements.")
    if _festival["category"] == "muslim" and "moon" not in _festival["date_note"]:
        _festival["date_note"] += " Subject to local moon-sighting announcements."


_MORE_DATE_SOURCES = {
    "Teachers' Day": ("https://www.pib.gov.in/PressNoteDetails.aspx?ModuleId=3&NoteId=152100&lang=2&reg=3", "Fixed annual Indian observance on 5 September."),
    "Guru Purnima": ("https://aphc.gov.in/docs/calendars/dc_2026.pdf", "29 July follows the Andhra Pradesh High Court 2026 calendar; check local observances."),
    "Sharad Navratri Begins": ("https://www.incredibleindia.gov.in/en/festivals-and-events/navratri", "Starts 11 October; Incredible India lists celebrations through 19 October 2026.")
}
for _festival in FESTIVALS_2026:
    if _festival["name"] in _MORE_DATE_SOURCES:
        _url, _note = _MORE_DATE_SOURCES[_festival["name"]]
        _festival.update(source_urls=[_url], date_note=_note)


def _font(name: str, size: int):
    return ImageFont.truetype(os.path.join(FONT_DIR, f"{name}.ttf"), size)


def _headline_font(style: str, size: int):
    """style is 'serif' (Playfair Display, bold instance of the variable font)
    or 'sans' (Poppins ExtraBold) - picked per-festival for typographic
    variety across the calendar."""
    if style == "serif":
        f = ImageFont.truetype(os.path.join(FONT_DIR, "PlayfairDisplay-Variable.ttf"), size)
        try:
            f.set_variation_by_name("Bold")
        except Exception:
            pass
        return f
    return _font("Poppins-ExtraBold", size)


def _tracked_width(draw, text: str, font, tracking: float) -> float:
    if not text:
        return 0
    return sum(draw.textlength(ch, font=font) for ch in text) + tracking * max(len(text) - 1, 0)


def _draw_tracked(draw, x: float, y: float, text: str, font, fill, tracking: float):
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking


def _draw_text_shadowed(draw, xy, text, font, fill, shadow=(8, 6, 4, 200), offset=(0, 3)):
    """Draws a soft dark shadow copy first, then the text on top - a standard,
    robust legibility technique for text over a photo/illustration whose
    exact local colour/luminance can't be guaranteed, complementing (not
    replacing) the scrim behind the whole block."""
    x, y = xy
    draw.text((x + offset[0], y + offset[1]), text, font=font, fill=shadow)
    draw.text(xy, text, font=font, fill=fill)


def _draw_tracked_shadowed(draw, x, y, text, font, fill, tracking, shadow=(8, 6, 4, 200), offset=(0, 3)):
    for ch in text:
        _draw_text_shadowed(draw, (x, y), ch, font, fill, shadow=shadow, offset=offset)
        x += draw.textlength(ch, font=font) + tracking


def _resolve_zone(placement: str) -> dict:
    """Maps a blueprint's free-text placement description (e.g. "left-center",
    "lower area", "upper-left third") to an anchor fraction of canvas
    width/height plus a text alignment - a small, forgiving keyword match
    since these are natural-language descriptions, not coordinates. Defaults
    to a lower-left placement (the spec's own worked-example pattern) when
    nothing recognisable is found."""
    p = (placement or "").lower()
    if "right" in p:
        x, align = 0.92, "right"
    elif "center" in p and "left" not in p and "upper" not in p and "lower" not in p:
        x, align = 0.5, "center"
    else:
        x, align = 0.08, "left"
    if "upper" in p or "top" in p:
        y = 0.14
    elif "lower" in p or "bottom" in p or "below" in p:
        y = 0.78
    else:
        y = 0.5
    return {"x": x, "y": y, "align": align}


def _resolve_corner(placement: str) -> tuple:
    """Maps a blueprint's logo_safe_zone.position text to an (h, v) corner
    pair, defaulting to upper-left (the spec's worked-example logo spot)."""
    p = (placement or "").lower()
    h = "right" if "right" in p else "left"
    v = "lower" if ("lower" in p or "bottom" in p) else "upper"
    return h, v


def _text_scrim(size: tuple, center: tuple, radius: float, max_alpha: int = 190) -> Image.Image:
    """A soft, edgeless radial darkening behind a text block - the spec's
    'restrained translucent treatment' for contrast, deliberately not a
    card/box: no defined edges, no uniform fill, just enough falloff that
    text reads clearly over whatever the illustration put there. Two-layer
    (a smaller, denser core plus a wider, softer falloff) so the area
    directly behind the glyphs is reliably dark even against a busy or
    light AI-generated background, not just visually "assisted"."""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    cx, cy = center
    core = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(core).ellipse([cx - radius * 0.65, cy - radius * 0.65, cx + radius * 0.65, cy + radius * 0.65],
                                  fill=(8, 6, 5, max_alpha))
    layer.alpha_composite(core.filter(ImageFilter.GaussianBlur(radius * 0.35)))
    wide = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(wide).ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                                  fill=(8, 6, 5, int(max_alpha * 0.55)))
    layer.alpha_composite(wide.filter(ImageFilter.GaussianBlur(radius * 0.7)))
    return layer


def _calmer_corner(img: Image.Image) -> str:
    """Which top corner ('left' or 'right') has less visual complexity, so the
    logo badge can sit somewhere that doesn't collide with the busiest part
    of the illustration."""
    w, h = img.size
    bw, bh = int(w * 0.36), int(h * 0.30)
    left = img.crop((0, 0, bw, bh)).convert("L")
    right = img.crop((w - bw, 0, w, bh)).convert("L")
    left_var = ImageStat.Stat(left).stddev[0]
    right_var = ImageStat.Stat(right).stddev[0]
    return "left" if left_var <= right_var else "right"


def _hex2rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _diagonal_gradient(size, colors):
    w, h = size
    rgbs = [_hex2rgb(c) for c in colors]
    n = len(rgbs) - 1
    base = Image.new("RGB", (w, h))
    px = base.load()
    diag = w + h
    for y in range(h):
        for x in range(0, w, 2):
            t = min(max((x + y) / diag, 0), 1)
            seg = min(int(t * n), n - 1)
            local_t = (t * n) - seg
            c0, c1 = rgbs[seg], rgbs[seg + 1]
            r = int(c0[0] + (c1[0] - c0[0]) * local_t)
            g = int(c0[1] + (c1[1] - c0[1]) * local_t)
            b = int(c0[2] + (c1[2] - c0[2]) * local_t)
            px[x, y] = (r, g, b)
            if x + 1 < w:
                px[x + 1, y] = (r, g, b)
    return base


def _blob_glow(size, center, radius, color, alpha=95, blur=None):
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx, cy = center
    d.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=(*color, alpha))
    return layer.filter(ImageFilter.GaussianBlur(blur if blur else radius * 0.55))


def _flame(d, x, y, scale=1.0):
    top_r = 15 * scale
    d.ellipse([x - top_r, y - 38 * scale - top_r, x + top_r, y - 38 * scale + top_r], fill=(255, 200, 80, 255))
    d.polygon([(x - top_r * 0.95, y - 38 * scale), (x, y), (x + top_r * 0.95, y - 38 * scale)], fill=(255, 200, 80, 255))
    in_r = top_r * 0.5
    d.ellipse([x - in_r, y - 30 * scale - in_r, x + in_r, y - 30 * scale + in_r], fill=(255, 245, 210, 255))
    d.polygon([(x - in_r * 0.9, y - 30 * scale), (x, y - 6 * scale), (x + in_r * 0.9, y - 30 * scale)], fill=(255, 245, 210, 255))


def _star_points(cx, cy, sr, rot=0.0):
    pts = []
    for i in range(10):
        ang = rot + math.pi / 2 + i * math.pi / 5
        rad = sr if i % 2 == 0 else sr * 0.42
        pts.append((cx + rad * math.cos(ang), cy - rad * math.sin(ang)))
    return pts


def _motif_diya(layer, rng, accent):
    w, h = layer.size
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    d = ImageDraw.Draw(layer, "RGBA")
    for row_y, n, scale in [(h * 0.70, 7, 1.0), (68, 5, 0.7)]:
        for i in range(n):
            x = w * (0.10 + i * 0.80 / max(n - 1, 1)) + rng.uniform(-14, 14)
            y = row_y + rng.uniform(-14, 10)
            gd.ellipse([x - 38 * scale, y - 55 * scale, x + 38 * scale, y + 20 * scale], fill=(255, 180, 70, 150))
            d.ellipse([x - 27 * scale, y - 5 * scale, x + 27 * scale, y + 17 * scale], fill=(110, 55, 22, 255))
            d.ellipse([x - 22 * scale, y - 9 * scale, x + 22 * scale, y + 5 * scale], fill=(190, 100, 40, 255))
            _flame(d, x, y - 2 * scale, scale)
    layer.alpha_composite(glow.filter(ImageFilter.GaussianBlur(22)))
    return layer


def _motif_confetti(layer, rng, accent):
    w, h = layer.size
    colors = ["#FF3D7F", "#FFC93C", "#2FD4A5", "#54A6FF", "#C86BFA", "#FF8A3D"]
    d = ImageDraw.Draw(layer, "RGBA")
    for _ in range(160):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        r = rng.uniform(5, 26)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(*_hex2rgb(rng.choice(colors)), int(rng.uniform(90, 200))))
    for _ in range(40):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        r = rng.uniform(2, 5)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, int(rng.uniform(140, 220))))
    return layer


def _motif_crescent_star(layer, rng, accent):
    w, h = layer.size
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    cx, cy, r = w * 0.76, h * 0.24, 190
    gd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 224, 130, 170))
    layer.alpha_composite(glow.filter(ImageFilter.GaussianBlur(55)))

    moon_r = 130
    base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(base).ellipse([cx - moon_r, cy - moon_r, cx + moon_r, cy + moon_r], fill=(255, 214, 110, 255))
    cut = moon_r * 0.60
    hole = Image.new("L", (w, h), 0)
    ImageDraw.Draw(hole).ellipse([cx - moon_r + cut, cy - moon_r - 14, cx + moon_r + cut, cy + moon_r - 14], fill=255)
    a = base.split()[3]
    base.putalpha(Image.composite(Image.new("L", (w, h), 0), a, hole))
    layer.alpha_composite(base)

    d = ImageDraw.Draw(layer, "RGBA")
    gold = (255, 214, 110, 255)
    d.polygon(_star_points(cx - 190, cy + 70, 30), fill=gold)
    d.polygon(_star_points(cx - 260, cy - 40, 16), fill=gold)
    d.polygon(_star_points(cx + 40, cy + 190, 14), fill=gold)
    n = 12
    for i in range(n):
        x = w * (i + 0.5) / n
        y = h * 0.72
        s = 16
        d.polygon([(x, y - s), (x + s, y), (x, y + s), (x - s, y)], fill=(*_hex2rgb(accent), 90))
    return layer


def _motif_rangoli(layer, rng, accent):
    w, h = layer.size
    d = ImageDraw.Draw(layer, "RGBA")
    palette = ["#FF6B6B", "#FFD93D", "#4ECDC4", "#FF922B", "#F783AC", "#63E6BE"]
    for (cx, cy, scale) in [(w * 0.5, h * 0.72, 0.75), (w * 0.14, h * 0.16, 0.5), (w * 0.88, h * 0.14, 0.45)]:
        for ring, radius in enumerate([260, 210, 160, 110, 60]):
            radius *= scale
            n = max(int((14 - ring * 2) * scale + 4), 6)
            col = _hex2rgb(palette[(ring + int(cx)) % len(palette)])
            for i in range(n):
                ang = 2 * math.pi * i / n
                x = cx + radius * math.cos(ang)
                y = cy + radius * 0.38 * math.sin(ang) - 30 * scale
                r = (9 - ring * 0.7) * scale
                if r > 1:
                    d.ellipse([x - r, y - r, x + r, y + r], fill=(*col, 220))
    return layer


def _motif_tricolor(layer, rng, accent):
    w, h = layer.size
    d = ImageDraw.Draw(layer, "RGBA")
    band_h = 34
    d.rectangle([0, 0, w, band_h], fill=(255, 153, 51, 255))
    d.rectangle([0, band_h, w, band_h * 2], fill=(255, 255, 255, 255))
    d.rectangle([0, band_h * 2, w, band_h * 3], fill=(19, 136, 8, 255))
    d.rectangle([0, h - band_h * 3, w, h - band_h * 2], fill=(19, 136, 8, 255))
    d.rectangle([0, h - band_h * 2, w, h - band_h], fill=(255, 255, 255, 255))
    d.rectangle([0, h - band_h, w, h], fill=(255, 153, 51, 255))
    cx, cy, r = w * 0.5, h * 0.685, 70
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(11, 39, 135, 255), width=6)
    d.ellipse([cx - 9, cy - 9, cx + 9, cy + 9], fill=(11, 39, 135, 255))
    for i in range(24):
        ang = 2 * math.pi * i / 24
        x1, y1 = cx + 10 * math.cos(ang), cy + 10 * math.sin(ang)
        x2, y2 = cx + r * math.cos(ang), cy + r * math.sin(ang)
        d.line([x1, y1, x2, y2], fill=(11, 39, 135, 255), width=3)
    return layer


def _motif_bloom(layer, rng, accent):
    w, h = layer.size
    d = ImageDraw.Draw(layer, "RGBA")
    for (cx, cy, scale, op) in [(w * 0.88, h * 0.88, 1.0, 160), (w * 0.1, h * 0.1, 0.55, 120), (w * 0.85, h * 0.12, 0.4, 100)]:
        for i in range(8):
            ang = 2 * math.pi * i / 8
            px_, py_ = cx + 74 * scale * math.cos(ang), cy + 74 * scale * math.sin(ang)
            d.ellipse([px_ - 48 * scale, py_ - 32 * scale, px_ + 48 * scale, py_ + 32 * scale], fill=(255, 255, 255, op))
        d.ellipse([cx - 26 * scale, cy - 26 * scale, cx + 26 * scale, cy + 26 * scale], fill=(*_hex2rgb(accent), 230))
    return layer


def _motif_snow(layer, rng, accent):
    w, h = layer.size
    d = ImageDraw.Draw(layer, "RGBA")
    for _ in range(130):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        r = rng.uniform(2, 6)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, int(rng.uniform(140, 230))))
    for _ in range(16):
        x, y = rng.uniform(50, w - 50), rng.uniform(40, h - 40)
        sr = rng.uniform(10, 20)
        d.polygon(_star_points(x, y, sr), fill=(255, 255, 255, 215))
    return layer


_MOTIFS = {
    "diya": _motif_diya, "confetti": _motif_confetti, "crescent_star": _motif_crescent_star,
    "rangoli": _motif_rangoli, "tricolor": _motif_tricolor, "bloom": _motif_bloom, "snow": _motif_snow,
}


def _wrap_text(draw, text, fnt, max_width):
    words = text.split()
    lines, cur = [], ""
    for word in words:
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=fnt) <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _fit_headline(draw, text, max_width, style="sans", start_size=92, min_size=40, max_height=None):
    """Shrinks the font until the wrapped headline fits both max_width (<=2
    lines) and, if given, max_height (the spec's festival-name-<=~18%-of-
    canvas-height rule) - whichever constraint binds first."""
    size = start_size
    while size >= min_size:
        fnt = _headline_font(style, size)
        lines = _wrap_text(draw, text, fnt, max_width)
        block_h = len(lines) * int(size * 1.12)
        if len(lines) <= 2 and (max_height is None or block_h <= max_height):
            return fnt, lines, size
        size -= 4
    fnt = _headline_font(style, min_size)
    return fnt, _wrap_text(draw, text, fnt, max_width), min_size


def render_greeting_image(festival: dict, headline: str, subline: str = "From all of us at Hridaan Labs",
                           tagline: str = "", seed: Optional[int] = None,
                           ai_background: Optional[Image.Image] = None,
                           channel: str = DEFAULT_CHANNEL, blueprint: Optional[dict] = None) -> bytes:
    """Festive greeting image, sized per channel (see FORMAT_SPECS - no longer
    always a 1:1 square). If ai_background is supplied it's used as the scene
    and the local gradient/motif generator is skipped; the Hridaan Labs logo badge and
    greeting text are always rendered locally either way.

    Per the Sep 2026 art-direction spec: no opaque text card/banner. Text is
    drawn directly onto the artwork's own negative space, with only a soft
    edgeless scrim behind it for contrast where needed - never a solid box.
    When a blueprint (from run_greeting_blueprint_agent) is available, its
    typography.greeting.placement and logo_safe_zone.position decide WHERE
    text/logo land; otherwise a sensible lower-left default is used, matching
    the spec's own worked example. Layout/typography (headline font, which
    zone text lands in when no blueprint, motif) vary per-festival -
    deterministic via the seeded rng, so the calendar doesn't look like the
    same template with a new photo."""
    rng = random.Random(seed if seed is not None else int.from_bytes(hashlib.sha256(festival["name"].encode()).digest()[:4], "big") % 10_000)
    fmt = FORMAT_SPECS.get(channel, FORMAT_SPECS[DEFAULT_CHANNEL])
    W, H = fmt["width"], fmt["height"]
    palette = festival["palette"]
    motif = festival["motif"]
    luma = sum(_hex2rgb(palette[-1]))
    accent = palette[-1] if luma < 600 else palette[0]
    headline_style = "serif" if festival.get("persona") in {"heritage", "traditional", "formal"} else "sans" if festival.get("persona") in {"modern", "futuristic", "trendy"} else rng.choice(["sans", "serif"])

    if ai_background is not None:
        bg = ai_background.convert("RGBA")
        if bg.size != (W, H):
            bg = _cover_resize(bg, W, H)
    else:
        bg = _diagonal_gradient((W, H), palette).convert("RGBA")
        bg.alpha_composite(_blob_glow((W, H), (W * 0.08, H * 0.06), max(W, H) * 0.31, _hex2rgb(palette[-1])))
        bg.alpha_composite(_blob_glow((W, H), (W * 0.95, H * 0.98), max(W, H) * 0.35, _hex2rgb(palette[0]), alpha=90))
        motif_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        motif_layer = _MOTIFS.get(motif, _motif_bloom)(motif_layer, rng, accent)
        bg.alpha_composite(motif_layer)

    # --- Text zone: where the blueprint (or, lacking one, a sensible default)
    # says the greeting goes. Resolved before drawing anything so the logo
    # and scrim can avoid colliding with it. ---
    greeting_placement = ((blueprint or {}).get("typography", {}).get("greeting", {}) or {}).get("placement", "lower-left third")
    max_height_pct = ((blueprint or {}).get("typography", {}).get("greeting", {}) or {}).get("max_height_pct", 18)
    zone = _resolve_zone(greeting_placement)

    # --- Hridaan Labs logo badge: compact, sits in the blueprint's logo safe zone if
    # one was planned, otherwise whichever top corner is visually calmer. ---
    brand = festival.get("brand", "Hridaan Labs")
    badge = None
    uploaded = festival.get("logo_image")
    if uploaded is not None or (brand and festival.get("sender_type", "company") == "company"):
        logo = uploaded.copy().convert("RGBA") if uploaded is not None else Image.new("RGBA", (640, 120), (255, 255, 255, 0))
        if uploaded is None:
            logo_draw = ImageDraw.Draw(logo)
            logo_font_size = 48
            while logo_font_size > 18 and logo_draw.textlength(brand, font=_font("Poppins-SemiBold", logo_font_size)) > 610:
                logo_font_size -= 2
            logo_draw.text((320, 60), brand, font=_font("Poppins-SemiBold", logo_font_size), fill=(20, 31, 30, 255), anchor="mm")
        logo.thumbnail((int(W*.25), int(H*.09)), Image.LANCZOS)
        pad=20
        badge=Image.new("RGBA", (logo.width+pad*2, logo.height+pad*2), (0,0,0,0))
        ImageDraw.Draw(badge).rounded_rectangle((0,0,badge.width-1,badge.height-1),radius=16,fill=(255,255,255,245))
        badge.alpha_composite(logo,(pad,pad))
        # Consistent top-right identity zone avoids the greeting and date below.
        if zone["y"] < .3:
            zone = _resolve_zone("lower-left third")

    # --- Greeting text: drawn directly on the artwork's negative space, no
    # card/banner. A soft edgeless scrim sits behind it only for contrast. ---
    tmp_draw = ImageDraw.Draw(bg)
    max_text_w = int(W * 0.42) if zone["align"] != "center" else int(W * 0.78)
    max_headline_h = H * (min(max_height_pct, 20) / 100.0)
    hd_font, head_lines, hsize = _fit_headline(tmp_draw, headline, max_text_w, style=headline_style, max_height=max_headline_h)
    sub_font_size = max(24, int(hsize * 0.32))
    sub_font = _font("Poppins-Medium", sub_font_size)
    eyebrow_font = _font("Poppins-Regular", max(20, int(hsize * 0.26)))
    eyebrow_text = tagline.strip().upper() if tagline else ""
    while sub_font_size > 16 and tmp_draw.textlength(subline, font=sub_font) > max_text_w:
        sub_font_size -= 1
        sub_font = _font("Poppins-Medium", sub_font_size)
    eyebrow_size = max(20, int(hsize * 0.26))
    while eyebrow_size > 12 and _tracked_width(tmp_draw, eyebrow_text, eyebrow_font, 3) > max_text_w:
        eyebrow_size -= 1
        eyebrow_font = _font("Poppins-Regular", eyebrow_size)

    line_h = int(hsize * 1.12)
    eyebrow_block_h = int(sub_font_size * 1.4) if eyebrow_text else 0
    date_text = festival.get("date_label", "")
    date_font = _font("Poppins-Regular", 20)
    sub_block_h = (int(sub_font_size * 1.5) if subline else 0) + (34 if date_text else 0)
    block_h = eyebrow_block_h + len(head_lines) * line_h + sub_block_h
    block_w = max([tmp_draw.textlength(line, font=hd_font) for line in head_lines] + [tmp_draw.textlength(subline, font=sub_font)])

    anchor_x = zone["x"] * W
    anchor_y = zone["y"] * H
    if zone["align"] == "left":
        text_left = anchor_x
    elif zone["align"] == "right":
        text_left = anchor_x - block_w
    else:
        text_left = anchor_x - block_w / 2
    text_top = anchor_y - block_h / 2
    text_top = max(40, min(text_top, H - block_h - 40))

    text_left = max(40, min(text_left, W - block_w - 40))
    scrim_cx, scrim_cy = text_left + block_w / 2, text_top + block_h / 2
    scrim_radius = max(block_w, block_h) * 0.8 + 90
    bg.alpha_composite(_text_scrim((W, H), (scrim_cx, scrim_cy), scrim_radius))

    d = ImageDraw.Draw(bg)
    ty = text_top
    # All greeting text uses one consistent high-contrast white scheme (not
    # accent-tinted) plus a soft dark shadow on every draw - the scrim above
    # only guarantees the *area* behind text is darker, not by how much
    # everywhere within its soft falloff, so the shadow is a second,
    # per-glyph line of defence against whatever the AI background actually
    # put there.
    headline_fill = (255, 255, 255, 255)
    subline_fill = (255, 255, 255, 235)
    eyebrow_fill = (255, 255, 255, 220)
    shadow = (6, 5, 4, 215)
    if eyebrow_text:
        ew = _tracked_width(d, eyebrow_text, eyebrow_font, tracking=3)
        ex = text_left if zone["align"] != "center" else scrim_cx - ew / 2
        if zone["align"] == "right":
            ex = text_left + block_w - ew
        _draw_tracked_shadowed(d, ex, ty, eyebrow_text, eyebrow_font, eyebrow_fill, tracking=3, shadow=shadow)
        ty += eyebrow_block_h
    for line in head_lines:
        tw = d.textlength(line, font=hd_font)
        lx = text_left if zone["align"] != "center" else scrim_cx - tw / 2
        if zone["align"] == "right":
            lx = text_left + block_w - tw
        _draw_text_shadowed(d, (lx, ty), line, hd_font, headline_fill, shadow=shadow, offset=(0, 4))
        ty += line_h
    tw = d.textlength(subline, font=sub_font)
    slx = text_left if zone["align"] != "center" else scrim_cx - tw / 2
    if zone["align"] == "right":
        slx = text_left + block_w - tw
    _draw_text_shadowed(d, (slx, ty + int(sub_font_size * 0.3)), subline, sub_font, subline_fill, shadow=shadow, offset=(0, 2))

    if date_text:
        date_y = ty + (int(sub_font_size * 1.5) if subline else 0) + 5
        date_w = d.textlength(date_text, font=date_font)
        dx = text_left if zone["align"] == "left" else scrim_cx-date_w/2 if zone["align"] == "center" else text_left+block_w-date_w
        _draw_text_shadowed(d, (dx, date_y), date_text, date_font, (255,255,255,215), shadow=shadow, offset=(0,2))
    if badge is not None:
        bg.alpha_composite(badge,(W-badge.width-48,48))
    out = BytesIO()
    bg.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


# -----------------------------------------------------------------------
# OpenAI agent - short WhatsApp greeting text
# -----------------------------------------------------------------------
GREETING_SYSTEM = """You are the greetings agent for Hridaan Labs's WhatsApp broadcast list.
You write in Hridaan Labs's voice: warm, direct, never generic corporate filler.

Input: a festival's name and a one-line description of what it marks.

Write TWO things:

1. whatsapp_message: ONE short WhatsApp broadcast message (2-4 sentences,
   under 300 characters) wishing the Hridaan Labs community well for the
   festival. It should:
   - Name the festival naturally (don't just repeat the input verbatim).
   - Feel warm and specific to a brand community, not a generic
     mass-market greeting - a brief, genuine touch is fine (e.g. tying it to
     togetherness, new beginnings, gratitude) but do not force a business/
     career angle onto every festival - a wish that just wishes well is fine.
   - Use at most one emoji, only if it fits naturally.
   - Not invent any claim, statistic, or event Hridaan Labs is hosting.

2. tagline: a short one-liner (3-7 words, no ending punctuation) that will
   sit as a small, understated line ABOVE the big "Happy <Festival>!"
   headline on the greeting image. It must:
   - Say something different from the headline, not restate the festival
     name - a feeling, a wish, or what the day represents (e.g. "Light over
     darkness, always", "A season of new beginnings", "Gratitude for those
     who teach us").
   - Read as quiet and warm, not a shout - this is the subtle line, not the
     headline.
   - Go out to customers, colleagues, friends and partners alike, so
     keep it dignified and inclusive, not casual slang.

This greeting is seen by customers, colleagues, friends and partners,
so both pieces of text should feel considered, not templated."""

GREETING_SCHEMA = {
    "type": "object",
    "properties": {
        "whatsapp_message": {"type": "string"},
        "tagline": {"type": "string"},
    },
    "required": ["whatsapp_message", "tagline"],
    "additionalProperties": False,
}


async def run_greeting_text_agent(festival: dict) -> dict:
    client = agents.get_client()
    user = f"Festival: {festival['name']}\nWhat it marks: {festival['blurb']}"
    response = await agents._create(
        client,
        system=GREETING_SYSTEM,
        user=user + "\nTone: " + festival.get("tone", "warm and respectful") + "\nRegional context: " + ", ".join(festival.get("regions", [])),
        schema=GREETING_SCHEMA,
        max_tokens=4000,
    )
    data = agents.extract_json_object(agents._first_text(response))
    return {"whatsapp_message": data["whatsapp_message"], "tagline": data["tagline"]}

