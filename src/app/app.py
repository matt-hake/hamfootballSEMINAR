#Written by Google Gemini
import streamlit as st
from pathlib import Path
from hamscrape import discover_pdf_records, YEAR
from ham_parser import parse

# Ensure export directories exist
EXPORTS_DIR = Path("data/exports")
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

st.title("NESCAC Parser Pipeline")

# --- UI Layout ---
col1, col2 = st.columns(2)
with col1:
    year = st.number_input("Year", value=YEAR)
with col2:
    limit = st.number_input("Limit links (demo safety)", value=0)

schools = ["Amherst", "Bates", "Bowdoin", "Colby", "Hamilton", "Middlebury", "Trinity", "Tufts", "Wesleyan", "Williams"]
selected_schools = st.multiselect("Select Schools:", schools)

# --- 1. The Discovery Step ---
if st.button("Find Games"):
    # Streamlit's built-in loading spinner replaces the HTML status text
    with st.spinner("Scraping NESCAC sites..."):
        try:
            # We call your hamscrape engine directly!
            results = discover_pdf_records(
                year=year,
                schools=selected_schools if selected_schools else schools,
                limit_links=limit
            )
            
            # Save the results to Streamlit's session state so they don't disappear
            st.session_state['games'] = results
            st.success(f"Found {len(results)} games!")
            
        except Exception as e:
            st.error(f"Scraping failed: {e}")

# --- 2. The Parsing Step ---
# If games are found, display them as interactive cards
if 'games' in st.session_state:
    st.markdown("---")
    st.subheader("Discovered Games")
    
    for game in st.session_state['games']:
        game_title = f"{game['school']} vs {game['opponent']} ({game['date']})"
        
        # Streamlit expanders act like your CSS cards
        with st.expander(game_title):
            st.write(f"**PDF Link:** {game['pdf_url']}")
            
            # When clicked, this button triggers your parser script
            if st.button(f"Parse {game_title}", key=game['pdf_url']):
                with st.spinner("Extracting plays and querying LLM..."):
                    
                    output_filename = EXPORTS_DIR / f"{game['school']}_{game['opponent']}.xlsx"
                    
                    try:
                        # Call your ham_parser engine directly!
                        final_excel_path = parse(game['pdf_url'], output_filename)
                        
                        st.success("Playbook verified and saved!")
                        
                        # Create a direct download button for the Excel file
                        with open(final_excel_path, "rb") as file:
                            st.download_button(
                                label="Download Verified Excel",
                                data=file,
                                file_name=final_excel_path.name,
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                            )
                            
                    except Exception as e:
                        st.error(f"Parsing failed: {e}")