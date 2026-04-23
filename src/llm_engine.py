# llm_engine.py
# Written by Gemini
# Handles all LLM-based extraction for the football PDF parser.

import os
import json
import time
from typing import Optional
from groq import Groq

# ------------------------------------------------------------
# API client setup
# ------------------------------------------------------------

api_key = os.getenv("GROQ_API_KEY")
if not api_key:
    raise RuntimeError(
        "GROQ_API_KEY is not set. Put it in your environment or .env file."
    )

client = Groq(api_key=api_key)

# ------------------------------------------------------------
# Shared defaults / helpers
# ------------------------------------------------------------

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

EXPECTED_KEYS = ["PASSER", "RUSHER", "RECEIVER", "DEFENDER", "DEFENSIVE PLAY TYPE"]

def normalize_feature_dict(result: Optional[dict], fallback: Optional[dict] = None) -> dict:
    if fallback is None:
        fallback = ERROR_FEATURES

    if not isinstance(result, dict):
        return fallback.copy()

    cleaned = {}

    for key in EXPECTED_KEYS:
        value = result.get(key, fallback[key])

        if isinstance(value, str):
            cleaned[key] = value.upper().strip()
        else:
            cleaned[key] = fallback[key]

        if cleaned[key] == "":
            cleaned[key] = fallback[key]

    return cleaned


def get_teams_from_title(header_text):
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

        t1 = str(result.get("team1", "")).upper().strip()
        t2 = str(result.get("team2", "")).upper().strip()

        if not t1:
            t1 = "UNKNOWN 1"
        if not t2:
            t2 = "UNKNOWN 2"

        return [t1, t2]

    except Exception as e:
        print(f"\nAPI failed to extract teams: {e}")
        return ["UNKNOWN 1", "UNKNOWN 2"]


# ------------------------------------------------------------
# Batch extraction for many plays
# ------------------------------------------------------------

def precompute_all_features(all_lines, chunk_size=15):
    all_features = []
    total_plays = len(all_lines)
    total_batches = (total_plays + chunk_size - 1) // chunk_size

    for i in range(0, total_plays, chunk_size):
        chunk = all_lines[i:i + chunk_size]

        # NEW: Provide a distinct index for each play in the prompt.
        plays_block = "\n".join([f"[ID: {idx}] {play}" for idx, play in enumerate(chunk)])

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
- If there is a DEFENDER, extract the DEFENSIVE PLAY TYPE (e.g., TACKLE, SACK, INTERCEPTION, PASS BREAKUP, FORCED FUMBLE). If there is no DEFENDER, use "NONE".
- Return ONLY valid JSON.

PLAYS:
{plays_block}

Return this exact JSON shape:
{{
  "plays": [
    {{"id": 0, "PASSER": "name", "RUSHER": "name", "RECEIVER": "name", "DEFENDER": "name", "DEFENSIVE PLAY TYPE": "type"}}
  ]
}}
"""

        max_retries = 3
        batch_success = False

        for attempt in range(max_retries):
            try:
                print(
                    f"\rAsking API: Batch {i // chunk_size + 1} of {total_batches} "
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

                # NEW: ALIGNMENT REPAIR VIA INDEX MAPPING
                # Pre-fill an array with errors exactly matching the chunk size
                aligned_batch = [ERROR_FEATURES.copy() for _ in range(len(chunk))]
                
                for res in batch_results:
                    play_id = res.get("id")
                    # If the LLM returned a valid ID that falls within our current chunk bounds, 
                    # slot it exactly where it belongs. This ignores hallucinated extra plays 
                    # and correctly leaves missed plays as ERROR_FEATURES.
                    if isinstance(play_id, int) and 0 <= play_id < len(chunk):
                        aligned_batch[play_id] = normalize_feature_dict(res, fallback=ERROR_FEATURES)

                actual_count = len(batch_results)
                if actual_count != len(chunk):
                    print(
                        f"\nWarning: AI returned {actual_count} items instead of {len(chunk)}. "
                        "Index mapping applied successfully."
                    )

                all_features.extend(aligned_batch)
                batch_success = True
                break

            except Exception as e:
                if "429" in str(e):
                    print(
                        "\nToken rate limit hit. Pausing for 10 seconds to let the token bucket refill..."
                    )
                    time.sleep(10)
                else:
                    print(f"\nAPI Error: {e}. Retrying in 2 seconds...")
                    time.sleep(2)

        if not batch_success:
            print("\nBatch failed. Filling with errors to save alignment.")
            all_features.extend([ERROR_FEATURES.copy() for _ in chunk])

        # Keep pacing between batches to reduce rate-limit pressure
        if (i + chunk_size) < total_plays:
            time.sleep(4)

    return all_features