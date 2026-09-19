# Extraction record

Source: https://github.com/ISMEBangalore/ISMESocialHub

Source branch: `main`

Source commit: `99d7c9055d9474cb72739b7d39935bc916ed2a33`

Extracted with the repository owner's instructions to create a separate product under their personal GitHub account. No write was made to the source repository.

## Reused

- `backend/festival_agents.py`: festival catalogue, artistic briefs and schemas, AI background generation, composition, text rendering, original template renderer and channel formats.
- `backend/assets/fonts/`: six bundled Poppins and Playfair Display fonts.
- Original Festivities workflow: choose → generate → review/edit → approve or reject → export.

## Adapted

- Institution-specific wording and fixed logo replaced with the chosen brand name.
- Newsletter/Anthropic helper dependency replaced with a small OpenAI Responses adapter. Only the new personal key is used.
- A stable SHA-256 seed replaces Python's process-randomized hash for consistent template output across restarts.
- Supporting text is fitted to its rendering zone.
- A standalone web interface replaces Social Hub navigation and React component dependencies.
- SQLite and local generated-image storage replace Social Hub MongoDB collections.
- A private studio password and session-owned collection replace Social Hub admin roles.
- WhatsApp broadcast side effects are omitted; approval saves a version for manual sharing.

The original source has no repository-wide LICENSE file at this snapshot. No new blanket open-source license is asserted for the inherited source. Font licensing is documented separately in `backend/assets/fonts/`.

API references used for the personal adapter:
- https://developers.openai.com/api/docs/guides/structured-outputs
- https://developers.openai.com/api/docs/models/gpt-image-2

