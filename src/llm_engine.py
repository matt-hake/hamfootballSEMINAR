# llm_engine.py
# Handles all LLM-based extraction for the football PDF parser.
#
# Packaging note:
# This file is designed to work both:
# 1. During normal development from the repository
# 2. Inside the packaged Windows app folder

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from groq import Groq


# ============================================================
# PATH SETUP
# ============================================================

# This file lives in:
#   src/llm_engine.py
SRC_DIR = Path(__file__).resolve().parent

# Project root:
#   hamfootballSEMINAR/
#
# In the packaged app, this becomes:
#   FootballParserApp/
PROJECT_DIR = SRC_DIR.parent

# Load environment variables from:
#   hamfootballSEMINAR/.env
#
# In the client package, this should be:
#   FootballParserApp/.env
load_dotenv(PROJECT_DIR / ".env")


# ============================================================
# API CLIENT SETUP
# ============================================================

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not GROQ_API_KEY:
    raise RuntimeError(
        "Missing GROQ_API_KEY. Make sure there is a .env file in the main app "
        "folder and that it contains a line like:\n\n"
        "GROQ_API_KEY=your_key_here"
    )

client = Groq(api_key=GROQ_API_KEY)


# ============================================================
# SHARED DEFAULTS / HELPERS
# ============================================================

ERROR_FEATURES = {
    "PASSER": "ERROR",
    "RUSHER": "ERROR",
    "RECEIVER": "ERROR",
    "DEFENDER": "ERROR",
    "DEFENSIVE PLAY TYPE": "ERROR",
}

NONE_FEATURES = {
    "PASSER": "NONE",
    "RUSHER": "NONE",
    "RECEIVER": "NONE",
    "DEFENDER": "NONE",
    "DEFENSIVE PLAY TYPE": "NONE",
}

EXPECTED_KEYS = [
    "PASSER",
    "RUSHER",
    "RECEIVER",
    "DEFENDER",
    "DEFENSIVE PLAY TYPE",
]


def normalize_feature_dict(
    result: Optional[dict],
    fallback: Optional[dict] = None,
) -> dict:
    """
    Normalize one LLM-produced feature dictionary.

    The LLM should return keys like:

        PASSER
        RUSHER
        RECEIVER
        DEFENDER
        DEFENSIVE PLAY TYPE

    This function makes sure every expected key exists and that every value
    is a clean uppercase string.

    If the LLM output is malformed, the fallback dictionary is used instead.
    """
    if fallback is None:
        fallback = ERROR_FEATURES

    if not isinstance(result, dict):
        return fallback.copy()

    cleaned = {}

    for key in EXPECTED_KEYS:
        value = result.get(key, fallback[key])

        if isinstance(value, str):
            cleaned_value = value.upper().strip()
        else:
            cleaned_value = fallback[key]

        if cleaned_value == "":
            cleaned_value = fallback[key]

        cleaned[key] = cleaned_value

    return cleaned


# ============================================================
# TEAM EXTRACTION
# ============================================================


def get_teams_from_title(header_text: str) -> list[str]:
    """
    Extract the two team names from the header text of a play-by-play document.

    Returns:
        A list like:

            ["HAMILTON", "AMHERST"]

        If extraction fails, returns:

            ["UNKNOWN 1", "UNKNOWN 2"]
    """
    prompt = f"""
Read the following header text from a football play-by-play document.

Extract the two teams playing in the game.
Ignore dates, times, and phrases like "Play by Play".

Return ONLY a JSON object in this format:
{{
  "team1": "First Team",
  "team2": "Second Team"
}}

HEADER TEXT:
"{header_text}"
"""

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )

        result = json.loads(response.choices[0].message.content)

        team_1 = str(result.get("team1", "")).upper().strip()
        team_2 = str(result.get("team2", "")).upper().strip()

        if not team_1:
            team_1 = "UNKNOWN 1"

        if not team_2:
            team_2 = "UNKNOWN 2"

        return [team_1, team_2]

    except Exception as error:
        print(f"\nAPI failed to extract teams: {error}")
        return ["UNKNOWN 1", "UNKNOWN 2"]


# ============================================================
# BATCH FEATURE EXTRACTION
# ============================================================


def precompute_all_features(
    all_lines: list[str],
    chunk_size: int = 15,
) -> list[dict]:
    """
    Ask the LLM to extract football features for every play line.

    Args:
        all_lines:
            List of play descriptions.

        chunk_size:
            Number of plays to send to the LLM in each request.

    Returns:
        A list of feature dictionaries.

    Important alignment behavior:
        Each play in a chunk is given a temporary ID.

        The LLM is asked to return that ID with each result.

        This lets us safely place each returned result back into the correct
        row, even if the LLM returns items slightly out of order or misses one.
    """
    all_features = []
    total_plays = len(all_lines)
    total_batches = (total_plays + chunk_size - 1) // chunk_size

    for start_index in range(0, total_plays, chunk_size):
        chunk = all_lines[start_index : start_index + chunk_size]

        plays_block = "\n".join(
            f"[ID: {play_id}] {play_text}"
            for play_id, play_text in enumerate(chunk)
        )

        prompt = f"""
Analyze these {len(chunk)} football plays.

For EACH play, identify:
- PASSER
- RUSHER
- RECEIVER
- DEFENDER
- DEFENSIVE PLAY TYPE

Rules:
- Return EXACTLY {len(chunk)} items in the "plays" array.
- Include the exact "id" integer provided for each play.
- If a role is missing, use "NONE".
- If there is a DEFENDER, extract the DEFENSIVE PLAY TYPE.
- Examples of DEFENSIVE PLAY TYPE include TACKLE, SACK, INTERCEPTION, PASS BREAKUP, FORCED FUMBLE.
- If there is no DEFENDER, use "NONE".
- Return ONLY valid JSON.

PLAYS:
{plays_block}

Return this exact JSON shape:
{{
  "plays": [
    {{
      "id": 0,
      "PASSER": "name",
      "RUSHER": "name",
      "RECEIVER": "name",
      "DEFENDER": "name",
      "DEFENSIVE PLAY TYPE": "type"
    }}
  ]
}}
"""

        max_retries = 3
        batch_success = False
        batch_number = start_index // chunk_size + 1

        for attempt in range(max_retries):
            try:
                print(
                    f"\rAsking API: Batch {batch_number} of {total_batches} "
                    f"(Attempt {attempt + 1}/{max_retries})...   ",
                    end="",
                    flush=True,
                )

                response = client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0,
                )

                raw_json = json.loads(response.choices[0].message.content)

                batch_results = raw_json.get("plays")

                if not isinstance(batch_results, list):
                    raise ValueError("Invalid LLM response format: missing 'plays' list")

                # Pre-fill the current batch with ERROR values.
                #
                # This preserves row alignment even if the LLM skips a play.
                aligned_batch = [ERROR_FEATURES.copy() for _ in range(len(chunk))]

                for result in batch_results:
                    if not isinstance(result, dict):
                        continue

                    play_id = result.get("id")

                    # Only accept IDs that correspond to this chunk.
                    # Ignore hallucinated IDs or malformed IDs.
                    if isinstance(play_id, int) and 0 <= play_id < len(chunk):
                        aligned_batch[play_id] = normalize_feature_dict(
                            result,
                            fallback=ERROR_FEATURES,
                        )

                if len(batch_results) != len(chunk):
                    print(
                        f"\nWarning: AI returned {len(batch_results)} items instead "
                        f"of {len(chunk)}. Index mapping was applied."
                    )

                all_features.extend(aligned_batch)
                batch_success = True
                break

            except Exception as error:
                error_message = str(error)

                if "429" in error_message:
                    print(
                        "\nToken rate limit hit. Pausing for 10 seconds before retrying..."
                    )
                    time.sleep(10)
                else:
                    print(f"\nAPI Error: {error}. Retrying in 2 seconds...")
                    time.sleep(2)

        if not batch_success:
            print("\nBatch failed. Filling with errors to preserve alignment.")
            all_features.extend(ERROR_FEATURES.copy() for _ in chunk)

        # Keep pacing between batches to reduce rate-limit pressure.
        if start_index + chunk_size < total_plays:
            time.sleep(4)

    return all_features