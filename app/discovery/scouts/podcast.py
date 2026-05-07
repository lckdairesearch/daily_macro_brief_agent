"""Podcast scout.

Steps:
  1. Query Listen Notes API for last-24h episodes from curated channels.
  2. LLM ranks and filters candidates against macro themes.
  3. Transcribe via OpenAI Whisper; fall back to Gemini for YouTube/audio.
  4. Extract thesis, evidence, and "what this means for our book."

To be implemented in Step 5.
"""

# Stub
