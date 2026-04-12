# ham_parser.py breaks down a pdf into individual plays and translates the key information to a spreadsheet
# Input: Play-by-Play PDF
# Output: Excel Sheet
import pdfplumber
import pandas as pd
import re
from llm_engine import get_teams_from_title, precompute_all_features
from pathlib import Path

# Issues
# 0. Add fumble and safety to series end data.
# 1. whenever there is a "no play" it should get its own line
# 2. series count breaks if team scores on kickoff
# 3. fixing distinguishing kick off, kickoff return fg, fg block etc
# 4. figure out how to get the same # of plays everytime
# 5. getting the last "gain" in a play

PAGE_START = 5 # indicates what page to start scraping on - 1
DOWN_POSSIBILITIES = ['1', '2', '3', '4']
FIELD_POSTION_LOCATION = 5 # where in the sentence the field postition can be found
NO_PLAY_WORDS = ["timeout", "time out", "end of game", "2:00"]

# get_raw_lines() gets all the lines from the pdf (starting at page 6)
def get_raw_lines(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        raw_lines = []
        for page in pdf.pages[PAGE_START:]:
            text = page.extract_text()
            if text:
                raw_lines.extend(text.split('\n'))
    return raw_lines

# Written by google gemini 3/30
# set_odk_team() looks at the literal first page in the document and asks the LLM to find the two teams
# return: Hamilton if playing, otherwise an arbitrarily chosen team
def set_odk_team(pdf_path):
    header_text = ""
    with pdfplumber.open(pdf_path) as pdf:
        if len(pdf.pages) > 0:
            text = pdf.pages[0].extract_text()
            if text:
                # FIX: Slices the first 20 lines properly
                header_text = "\n".join(text.split('\n')[:20]) 
    
    if header_text:
        print("Asking LLM to identify teams from the Title Page...")
        teams = get_teams_from_title(header_text)
        odk_team = teams[0] if teams[0] else "UNKNOWN" 
        
        for team in teams:
            if team and ("HAM" in team.upper() or "HAMILTON" in team.upper()):
                odk_team = team
                break
        return odk_team
    else:
        print("Error: Could not read the Title Page.")
        return "Error: Could not read the Title Page."

# quarter_update() increments the quarter if certain phrases are found in the current line.
def quarter_update(line):
    line = line.lower()
    if "start of quarter" in line or "start of 2nd half" in line:
        return True
    return False

# could break if kickoff not the third line
# not_play() makes checks to see if the line is actually a play, if not go to next line.
def not_play(line, line_number):
    line = line.lower()
    if not line or (line[0] not in DOWN_POSSIBILITIES and "kickoff" not in line):
        return True
    for word in NO_PLAY_WORDS:
        if word in line:
            return True
    return False

# check_fp_lost() checks if first column info got pushed onto the next line and puts it back in the current line.
def check_fp_lost(current_words, raw_lines, line_number, line):
    down_overflow = False
    if len(current_words) >= FIELD_POSTION_LOCATION:
        fifth_word = current_words[FIELD_POSTION_LOCATION - 1]
        # Check if fifth word ends in a digit (0-9)
        if not fifth_word[-1].isdigit():
            if line_number + 1 < len(raw_lines):
                next_line_words = raw_lines[line_number+1].split()
                if next_line_words:
                    # Gets first word of next line and place at index 4
                    current_words.insert(4, next_line_words[0])
                    line = " ".join(current_words)
                    down_overflow = True
    return current_words, line, down_overflow

# check_add_next_line() adds the next line to the current line if necessary. 
def check_add_next_line(raw_lines, line_number, line, down_overflow):
    while not line.endswith('.') and line_number + 1 < len(raw_lines):
        line_number += 1
        next_line_raw = raw_lines[line_number].strip()
        next_line_words = next_line_raw.split()
        
        # If down/distance data overflowed, skip first word when adding the next line. 
        if down_overflow:
            line += " " + " ".join(next_line_words[1:])
        else:
            line += " " + next_line_raw
    return line_number, line

#update_odk updates the odk variable if there is a change in possession
def update_odk(current_words, line, odk_team, odk):
    go_next_line = False
    if "drive start" in line:
        if current_words[FIELD_POSTION_LOCATION] == odk_team:
            odk = "OFF"
        else:
            odk = "DEF"
        go_next_line = True
    elif "kick" in line or "field goal" in line:
        odk = "FG"
        if "kickoff" in line:
            odk = "KO"
    elif "punt" in line:
        if odk == "OFF":
            odk = "P"
        else:
            odk = "PR"
    return odk, go_next_line

# blank_space_bug() breaks up the field position and the rest of the play if they get merged
def blank_space_bug(odk, current_words):
    if (odk == "OFF" or odk == "DEF"):
        try:
            int(current_words[FIELD_POSTION_LOCATION - 1][-1])
        except:
            current_words.pop(4)
            match = re.search(r"(\d+)([A-Z][a-z]+)", current_words[4])
            if match:
                split1 = current_words[FIELD_POSTION_LOCATION - 1][:match.end(1)]
                split2 = current_words[FIELD_POSTION_LOCATION - 1][match.start(2):]
                current_words[FIELD_POSTION_LOCATION - 1] = split1
                current_words.insert(FIELD_POSTION_LOCATION, split2)
    return current_words

def update_series_count(all_plays, series_trigger, odk, series_count):
    if not all_plays:
        return 1 if odk in ["OFF", "DEF"] else 0
    if odk == series_trigger and all_plays[-1]["ODK"] != odk:
        return series_count + 1
    return series_count

def update_run_pass(line):
    run_or_pass = ""
    if "rush" in line:
        run_or_pass = "R"
    elif "pass" in line or "sack" in line:
        run_or_pass = "P"
    return run_or_pass

def update_field_position(current_words, odk, odk_team):
    field_position = ""
    if odk == "OFF" or odk == "DEF":
        try:
            int(current_words[FIELD_POSTION_LOCATION - 1][-2])
            fp_digit_tracker = -2
        except:
            fp_digit_tracker = -1

        try:
            field_position = int(current_words[FIELD_POSTION_LOCATION - 1][fp_digit_tracker:])
            if odk == "DEF":
                if current_words[FIELD_POSTION_LOCATION - 1][:2].lower() != odk_team[:2].lower():
                    field_position *= -1
            elif odk == "OFF":
                if current_words[FIELD_POSTION_LOCATION - 1][:2].lower() == odk_team[:2].lower():
                    field_position *= -1
        except:
            field_position = ""

    if field_position == -50:
        field_position = 50
    return field_position

def update_gain(all_plays, field_position):
    if len(all_plays) >= 1:
        try:
            previous_position = int(all_plays[-1]["FIELD POSITION (YARDLINE)"])
            if field_position < 0:
                converted_field_position = field_position * -1
            else:
                converted_field_position = 100 - field_position
            if previous_position < 0:
                previous_position *= - 1
            else:
                previous_position = 100 - previous_position

            all_plays[-1]["GAIN"] = converted_field_position - previous_position
        except:
            all_plays[-1]["GAIN"] = ""
    return all_plays

def update_series_end(all_plays, line):
    line = line.lower()
    series_end = ""
    if "touchdown" in line:
        series_end = "TD"
    elif "punt" in line and len(all_plays) >= 1:
        all_plays[-1]["SERIES END"] = "PUNT"
    elif "intercepted" in line:
        series_end = "INT"
    if "end of half" in line:
        series_end = "HALF"
    return series_end

def update_distance(line):
    words = line.split()
    distance = words[2] if len(words) > (FIELD_POSTION_LOCATION - 1) else ""
    if distance.lower() == "GOAL".lower():
        try:
            return int(words[FIELD_POSTION_LOCATION - 1][-2:])
        except:
            return int(words[FIELD_POSTION_LOCATION - 1][-1])
    return distance

# Written by Gemini 
def find_direction(line):
    words = re.findall(r'\b\w+\b', line.lower())
    
    directions = {"left", "middle", "right"}
    modifiers = {"short", "deep"}
    
    for i, word in enumerate(words):
        if word in directions:
            if i > 0 and words[i-1] in modifiers:
                return f"{words[i-1]} {word}"
            else:
                return word
                
    # If the loop finishes and nothing was found, return an empty string
    return ""

"""
# main() controls the flow of the program 
# var pdf_path: the path the to play-by-play pdf
# var excel_output: the path to output the excel file
def parser(pdf_path, excel_output): 
    # var all_plays: a list of dictionaries that is copied to make the excel sheet
    # var odk: offense, defense, or special teams information
    # var series_trigger: the first team to have possession ("OFF" or "DEF")
    # var snap_count/series_count/play_count: snap/series/play count in current possession/series
    # var quarter: current quarter of the game
    # var down_overflow: true if the down information can't be found on the first line. 
    all_plays = []
    odk, series_trigger = None, None
    snap_count, series_count, play_count, quarter = 0, 0, 1, 1
    down_overflow = False
    
    raw_lines = get_raw_lines(pdf_path)
    odk_team = set_odk_team(pdf_path)

    line_number = 0
    while line_number < len(raw_lines):
        line = raw_lines[line_number].strip()

        if quarter_update(line):
            quarter += 1
        
        if not_play(line, line_number):
            line_number += 1
            continue

        current_words = line.split()

        if len(all_plays) > 1:
            current_words, line, down_overflow = check_fp_lost(current_words, raw_lines, line_number, line)
        
        line_number, line = check_add_next_line(raw_lines, line_number, line, down_overflow)

        # Update odk
        dummy_odk = odk
        odk, go_next_line = update_odk(current_words, line, odk_team, odk)
        if go_next_line:
            line_number += 1
            continue
        
        # Fixes bug where blank space after field position is ignored
        current_words = blank_space_bug(odk, current_words)
        
        # Update Snap Count
        snap_count = 0
        if len(all_plays) >= 1 and odk in ["DEF", "OFF"]:
            if odk == all_plays[-1]["ODK"]:
                snap_count = int(all_plays[-1]["SNAP #"]) + 1
            else:
                snap_count = 1
            
        # Update Series Count (THIS COULD BREAK IF TEAM SCORES ON KICKOFF)
        if series_trigger is None and odk in ["OFF", "DEF"]:
            series_trigger = odk
        series_count = update_series_count(all_plays, series_trigger, odk, series_count)
        
        # Look for Run/Pass
        run_or_pass = update_run_pass(line)
        rp_location = find_direction(line)
        
        # Get Field Position
        field_position = update_field_position(current_words, odk, odk_team)
        
        # Get Gain (doesn't yet count the last play in a possession)
        all_plays = update_gain(all_plays, field_position)

        # check if the series is over (only works for touchdowns and punts right now)
        series_end = update_series_end(all_plays, line)
            
        # Get Down (1st char) and Distance (4th word)
        down = line[0]
        distance = update_distance(line)
 
        # Updates the row with all the extracted features (not putting this in a function because too many variables)
        all_plays.append({
            "HUMAN VERIFIED?": "No",
            "PLAY #": play_count,
            "ODK": odk,
            "SERIES": series_count if odk == "DEF" or odk == "OFF" else "",
            "SNAP #": "" if snap_count == 0 else snap_count,
            "DOWN": int(down) if odk != "KO" else "",
            "DISTANCE": int(distance) if odk != "KO" else "",
            "FIELD POSITION (YARDLINE)": field_position,
            "GAIN": "",
            "HASH": "",
            "RUN/PASS": run_or_pass,
            "QUARTER": quarter,
            "SERIES END": series_end,
            "PASSER": "",
            "RUSHER": "",
            "RECEIVER": "",
            "DEFENDER": "",
            "DEFENSIVE PLAY TYPE": "",
            "Raw Text": line
        })

        odk = dummy_odk
        play_count += 1
        line_number += 1

        print(f"\rPlay #{play_count} complete... ", end="", flush=True)
    
    # written by Google Gemini 324 - 337
    # sends prompts to llm in chunks so that fewer prompts are required
    print("\n\n non-player logic complete! Filtering plays for LLM extraction...")
    
    # 1. Collect ONLY the OFF/DEF plays, keeping track of their exact row index
    plays_to_process = []
    original_indices = []

    for index, play in enumerate(all_plays):
        if play["ODK"] in ["OFF", "DEF"]:
            plays_to_process.append(play["Raw Text"])
            original_indices.append(index)
        else:
            # It's Special Teams. Default to NONE so the Excel cells aren't blank.
            play["PASSER"] = "NONE"
            play["RUSHER"] = "NONE"
            play["RECEIVER"] = "NONE"
            play["DEFENDER"] = "NONE"
    
    # 2. Run the chunking function ONLY on the filtered plays
    if plays_to_process:
        print(f"Skipping special teams. Sending {len(plays_to_process)} plays to LLM...")
        llm_results = precompute_all_features(plays_to_process, chunk_size=15)
        
        # 3. Stitch the LLM results back into the correct original dictionaries
        # zip() pairs up the original row number with the LLM's answer
        for original_index, features in zip(original_indices, llm_results):
            all_plays[original_index]["PASSER"] = features.get("PASSER", "NONE")
            all_plays[original_index]["RUSHER"] = features.get("RUSHER", "NONE")
            all_plays[original_index]["RECEIVER"] = features.get("RECEIVER", "NONE")
            all_plays[original_index]["DEFENDER"] = features.get("DEFENDER", "NONE")
    else:
        print("No offensive or defensive plays found.")
        
    all_plays[-1]["SERIES END"] = "GAME"
    df = pd.DataFrame(all_plays)
    df.to_excel(excel_output, index=False)
"""

#new functions for integration:

#slightly modified parser to return path for front end (CITE: ChatGPT 5.2)
def build_dataframe_from_pdf(pdf_path):
    all_plays = []
    odk, series_trigger = None, None
    snap_count, series_count, play_count, quarter = 0, 0, 1, 1
    down_overflow = False

    raw_lines = get_raw_lines(pdf_path)
    odk_team = set_odk_team(pdf_path)

    line_number = 0
    while line_number < len(raw_lines):
        line = raw_lines[line_number].strip()

        if quarter_update(line):
            quarter += 1

        if not_play(line, line_number):
            line_number += 1
            continue

        current_words = line.split()

        if len(all_plays) > 1:
            current_words, line, down_overflow = check_fp_lost(
                current_words, raw_lines, line_number, line
            )

        line_number, line = check_add_next_line(raw_lines, line_number, line, down_overflow)

        dummy_odk = odk
        odk, go_next_line = update_odk(current_words, line, odk_team, odk)
        if go_next_line:
            line_number += 1
            continue

        current_words = blank_space_bug(odk, current_words)

        snap_count = 0
        if len(all_plays) >= 1 and odk in ["DEF", "OFF"]:
            if odk == all_plays[-1]["ODK"]:
                snap_count = int(all_plays[-1]["SNAP #"]) + 1
            else:
                snap_count = 1

        if series_trigger is None and odk in ["OFF", "DEF"]:
            series_trigger = odk
        series_count = update_series_count(all_plays, series_trigger, odk, series_count)

        run_or_pass = update_run_pass(line)
        rp_location = find_direction(line)
        field_position = update_field_position(current_words, odk, odk_team)
        all_plays = update_gain(all_plays, field_position)
        series_end = update_series_end(all_plays, line)

        down = line[0]
        distance = update_distance(line)

        all_plays.append({
            "HUMAN VERIFIED?": "No",
            "PLAY #": play_count,
            "ODK": odk,
            "SERIES": series_count if odk in ["DEF", "OFF"] else "",
            "SNAP #": "" if snap_count == 0 else snap_count,
            "DOWN": int(down) if odk != "KO" else "",
            "DISTANCE": int(distance) if odk != "KO" and str(distance).isdigit() else distance,
            "FIELD POSITION (YARDLINE)": field_position,
            "GAIN": "",
            "HASH": "",
            "RUN/PASS": run_or_pass,
            "QUARTER": quarter,
            "SERIES END": series_end,
            "R/P Location": rp_location,
            "Close Game/Blowout": "",
            "PASSER": "",
            "RUSHER": "",
            "RECEIVER": "",
            "DEFENDER": "",
            "DEFENSIVE PLAY TYPE": "",
            "Raw Text": line,
        })

        odk = dummy_odk
        play_count += 1
        line_number += 1

        print(f"\rPlay #{play_count} complete... ", end="", flush=True)

    print("\n\nnon-player logic complete! Filtering plays for LLM extraction...")

    plays_to_process = []
    original_indices = []

    for index, play in enumerate(all_plays):
        if play["ODK"] in ["OFF", "DEF"]:
            plays_to_process.append(play["Raw Text"])
            original_indices.append(index)
        else:
            play["PASSER"] = "NONE"
            play["RUSHER"] = "NONE"
            play["RECEIVER"] = "NONE"
            play["DEFENDER"] = "NONE"
            play["DEFENSIVE PLAY TYPE"] = "NONE"  # Added this for special teams

    if plays_to_process:
        print(f"Skipping special teams. Sending {len(plays_to_process)} plays to LLM...")
        llm_results = precompute_all_features(plays_to_process, chunk_size=15)

        for original_index, features in zip(original_indices, llm_results):
            all_plays[original_index]["PASSER"] = features.get("PASSER", "NONE")
            all_plays[original_index]["RUSHER"] = features.get("RUSHER", "NONE")
            all_plays[original_index]["RECEIVER"] = features.get("RECEIVER", "NONE")
            
            # Grab the defender first
            defender = features.get("DEFENDER", "NONE")
            all_plays[original_index]["DEFENDER"] = defender
            
            # Conditional logic: Only record a defensive play type if a defender exists
            if defender != "NONE" and defender != "":
                all_plays[original_index]["DEFENSIVE PLAY TYPE"] = features.get("DEFENSIVE PLAY TYPE", "NONE")
            else:
                all_plays[original_index]["DEFENSIVE PLAY TYPE"] = "NONE"
    else:
        print("No offensive or defensive plays found.")

    if all_plays:
        all_plays[-1]["SERIES END"] = "GAME"

    return pd.DataFrame(all_plays)

#CITE: ChatGPT 5.2
def parse(pdf_path, excel_output):
    """
    Parse one play-by-play PDF and write one Excel file.

    Returns:
        Path: the final Excel output path
    """
    pdf_path = Path(pdf_path)
    excel_output = Path(excel_output)

    excel_output.parent.mkdir(parents=True, exist_ok=True)

    df = build_dataframe_from_pdf(pdf_path)

    if df.empty:
        raise ValueError(f"No plays were parsed from PDF: {pdf_path}")

    # Save exactly as you normally would, just swap the engine
    writer = pd.ExcelWriter(excel_output, engine='xlsxwriter')
    df.to_excel(writer, index=False, sheet_name='Plays')

    # Grab the worksheet and apply the rule to Column A (rows 2 through 1000)
    worksheet = writer.sheets['Plays']
    worksheet.data_validation('A2:A1000', {'validate': 'list', 'source': ['No', 'Yes']})

    writer.close()
    return excel_output

if __name__ == "__main__":
    parse("games/HamMid25.pdf", "parser_results/HamMid25.xlsx")