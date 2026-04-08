# llm_engine.py
#
# Purpose:
# Handle all LLM-based extraction for the football PDF parser.
#
# Major revisions in this version:
# 1. REMOVED hardcoded API key for security
# 2. MADE batch response parsing stricter and safer
# 3. REMOVED numbered play formatting that could confuse the model
# 4. ADDED alignment repair so batches do not fail just because the LLM
#    returned too many or too few items
# 5. ADDED response normalization helpers so every result has the exact
#    keys the parser expects

import os
import json
import time
from typing import Optional
from groq import Groq


# ------------------------------------------------------------
# API client setup
# ------------------------------------------------------------

# NEW:
# Do NOT hardcode the API key in source code.
# This file now relies on the environment variable GROQ_API_KEY.
#
# Example:
#   export GROQ_API_KEY="your_key_here"
# or load it through python-dotenv elsewhere in your app.
#
# This is much safer than:
#   os.environ["GROQ_API_KEY"] = "..."
#
api_key = os.getenv("GROQ_API_KEY")
if not api_key:
    raise RuntimeError(
        "GROQ_API_KEY is not set. Put it in your environment or .env file."
    )

client = Groq(api_key=api_key)


# ------------------------------------------------------------
# Shared defaults / helpers
# ------------------------------------------------------------

# NEW:
# Central default result so all fallback/error cases are consistent.
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


# NEW:
# Force every returned object into the exact schema expected by ham_parser.
# This prevents weird model outputs from breaking alignment or downstream code.
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


# NEW:
# Repair batch length mismatches instead of throwing away the whole batch.
#
# If the LLM returns:
# - too many items -> trim extras
# - too few items  -> pad with ERROR rows
#
# This is the most important fix for the issue you saw in the logs.
def repair_batch_alignment(batch_results, expected_count: int):
    if not isinstance(batch_results, list):
        batch_results = []

    actual_count = len(batch_results)

    if actual_count != expected_count:
        print(
            f"\nWarning: AI returned {actual_count} items instead of {expected_count}. Fixing alignment..."
        )

    if actual_count > expected_count:
        batch_results = batch_results[:expected_count]

    elif actual_count < expected_count:
        batch_results.extend(
            [ERROR_FEATURES.copy() for _ in range(expected_count - actual_count)]
        )

    return batch_results

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

        # NEW:
        # Removed "Play 1:", "Play 2:", etc.
        # Numbering can sometimes encourage the model to invent extra items
        # or otherwise get confused about the expected output count.
        #
        # We now pass plain bullet-style lines instead.
        plays_block = "\n".join([f"- {play}" for play in chunk])

        # NEW:
        # Stronger prompt language:
        # explicitly say EXACTLY how many objects should be returned.
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
- Keep the same order as the input plays.
- Do not skip any plays.
- Do not add any extra plays.
- If a role is missing, use "NONE".
- If there is a DEFENDER, extract the DEFENSIVE PLAY TYPE (e.g., TACKLE, SACK, INTERCEPTION, PASS BREAKUP, FORCED FUMBLE). If there is no DEFENDER, use "NONE".
- Return ONLY valid JSON.

PLAYS:
{plays_block}

Return this exact JSON shape:
{{
  "plays": [
    {{"PASSER": "name", "RUSHER": "name", "RECEIVER": "name", "DEFENDER": "name", "DEFENSIVE PLAY TYPE": "type"}}
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

                # NEW:
                # Be strict about the top-level response structure.
                # We expect {"plays": [...]} and treat anything else as malformed.
                batch_results = raw_json.get("plays")
                if not isinstance(batch_results, list):
                    raise ValueError("Invalid LLM response format: missing 'plays' list")

                # NEW:
                # Instead of rejecting mismatched-length batches and eventually
                # filling the whole chunk with errors, repair the length first.
                batch_results = repair_batch_alignment(batch_results, len(chunk))

                # NEW:
                # Normalize every returned object so all rows have exactly the
                # keys ham_parser expects.
                batch_results = [
                    normalize_feature_dict(res, fallback=ERROR_FEATURES)
                    for res in batch_results
                ]

                all_features.extend(batch_results)
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