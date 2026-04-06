import os
import json
import time
from groq import Groq

os.environ["GROQ_API_KEY"] = "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"
#client = Groq()

#recommended security tweak
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def extract_play_features(play_text):
    prompt = f"""
    Analyze the following play and identify the players involved.
    Extract the PASSER, RUSHER, RECEIVER, and DEFENDER.
    Output ONLY the player's name or jersey number.
    If a role is not present, the value MUST be "NONE".
    
    PLAY: "{play_text}"
    
    Respond in this exact JSON format:
    {{
      "PASSER": "name",
      "RUSHER": "name",
      "RECEIVER": "name",
      "DEFENDER": "name"
    }}
    """

    default_features = {"PASSER": "ERROR", "RUSHER": "ERROR", "RECEIVER": "ERROR", "DEFENDER": "ERROR"}

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0
        )
        
        result_dict = json.loads(response.choices[0].message.content)
        
        for key in result_dict:
            if isinstance(result_dict[key], str):
                result_dict[key] = result_dict[key].upper().strip()
                
        return result_dict

    except Exception as e:
        print(f"\nAPI Error on play: {play_text[:30]}... -> {e}")
        return default_features


def get_teams_from_title(header_text):
    prompt = f"""
    Read the following header text from a football play-by-play document.
    Extract the names or abbreviations of the two teams playing in the game.
    Ignore dates, times, and words like "Play by Play".
    
    HEADER TEXT: 
    "{header_text}"
    
    Respond in this exact JSON format:
    {{
      "team1": "First Team",
      "team2": "Second Team"
    }}
    """
    
    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0
        )
        
        result = json.loads(response.choices[0].message.content)
        t1 = result.get("team1", "").upper().strip()
        t2 = result.get("team2", "").upper().strip()
        
        return [t1, t2]
        
    except Exception as e:
        print(f"\nAPI failed to extract teams: {e}")
        return ["UNKNOWN 1", "UNKNOWN 2"]


def precompute_all_features(all_lines, chunk_size=15):
    all_features = []
    total_plays = len(all_lines)
    error_dict = {"PASSER": "ERROR", "RUSHER": "ERROR", "RECEIVER": "ERROR", "DEFENDER": "ERROR"}
    total_batches = (total_plays + chunk_size - 1) // chunk_size

    for i in range(0, total_plays, chunk_size):
        chunk = all_lines[i:i + chunk_size]
        numbered_plays = "\n".join([f"Play {j+1}: {play}" for j, play in enumerate(chunk)])
        
        # Explicitly asking for the 'plays' dictionary to avoid the JSON parsing crash
        prompt = f"""
        Analyze these {len(chunk)} football plays. Identify the PASSER, RUSHER, RECEIVER, and DEFENDER for each.
        If a role is missing, use "NONE".
        
        PLAYS:
        {numbered_plays}
        
        Respond STRICTLY in this JSON format. Do not include any other text:
        {{
          "plays": [
            {{"PASSER": "name", "RUSHER": "name", "RECEIVER": "name", "DEFENDER": "name"}},
            {{"PASSER": "name", "RUSHER": "name", "RECEIVER": "name", "DEFENDER": "name"}}
          ]
        }}
        """

        max_retries = 3
        batch_success = False

        for attempt in range(max_retries):
            try:
                print(f"\rAsking API: Batch {i//chunk_size + 1} of {total_batches} (Attempt {attempt + 1}/3)...   ", end="", flush=True)
                
                response = client.chat.completions.create(
                    model="llama-3.1-8b-instant",
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0
                )
                
                raw_json = json.loads(response.choices[0].message.content)
                
                # Safely handle both lists and dictionaries so the script never crashes
                if isinstance(raw_json, list):
                    batch_results = raw_json
                else:
                    batch_results = raw_json.get("plays", [])
                
                if len(batch_results) == len(chunk):
                    for res in batch_results:
                        for k in res:
                            if isinstance(res[k], str): res[k] = res[k].upper().strip()
                    all_features.extend(batch_results)
                    batch_success = True
                    break 
                else:
                    print(f"\nWarning: AI returned {len(batch_results)} items instead of {len(chunk)}. Retrying...")
                    time.sleep(2)

            except Exception as e:
                if "429" in str(e):
                    print(f"\nToken Rate limit hit! Pausing for 10 seconds to let the token bucket refill...")
                    time.sleep(10)
                else:
                    print(f"\nAPI Error: {e}. Retrying in 2 seconds...")
                    time.sleep(2)
                    
        if not batch_success:
            print(f"\nBatch failed. Filling with errors to save alignment.")
            all_features.extend([error_dict.copy() for _ in chunk])
            
        # Pacing out the batches to stay under the TPM limit
        if (i + chunk_size) < total_plays:
            time.sleep(4) 

    return all_features