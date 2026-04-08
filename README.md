#CITE: CHATGPT 5.2

# NESCAC Football PDF Pipeline (FastAPI Demo)

This project provides a **web-based interface** for running a Python data pipeline that:

1. Scrapes football boxscore PDFs from NESCAC athletics sites  
2. Deduplicates and verifies downloads  
3. Organizes outputs per job  
4. Allows users to download results through a browser  

The system is implemented as a **local demo** but is structured like a production system:
- FastAPI web server
- Background job execution
- Isolated per-job storage
- Clear separation between UI and data pipeline

The frontend (index.html) communicates with the backend through REST endpoints in api.py. The API validates user input and delegates scraping tasks to hamscrape.py, which performs the actual PDF discovery. The API then returns structured JSON responses that the UI renders dynamically.

This repository is intended for development, demonstration, and extension.

ChatGPT 5.2 was used to support the development of much of the scraping code and the 
html to set up early versions of the user interface.

---

## Prerequisites

- Python **3.10+** (3.11 recommended)
- macOS or Linux (Windows works with minor adjustments)
- Internet connection (pipeline makes live HTTP requests)

---

## 1. Installation and Setup (you only need to do this once!)
        a. Paste this link in your browser -> https://code.visualstudio.com/download 
        b. Download the download that corresponds to your operating system (Windows, Mac, Linux)
        c. Open the application
        d. Hit the following keys, For Mac: Cmd + Shift + `    For Windows: Ctrl + Shift + `
        e. In the window that opened copy and paste the following lines and hit enter after each one
                git clone https://github.com/hamfootballSEMINAR/hamfootballSEMINAR.git
                cd hamfootballSEMINAR
                python3 -m venv .venv
                source .venv/bin/activate
                pip install -r requirements.txt
                pip install fastapi "uvicorn[standard]" jinja2 pydantic requests beautifulsoup4 lxml flask
                

## 1. Create and Activate the Virtual Environment

From the **project root directory**:



```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install fastapi "uvicorn[standard]" jinja2 pydantic requests beautifulsoup4 lxml
uvicorn app.api:app --reload
python3 -m flask --app src/app/app.api --debug run 
pytest ../tests/test_hamscrape.py (from src)


pytest tests/test_hamscrape.py tests/test_validators.py -v

create_job(
    year=2025,
    schools=["Hamilton", "Williams"],
    verbose=True,
    limit_links=0,
    run_pytests=False,   # set True only when you explicitly want it
)

```markdown
User chooses schools / limits / output folder
        ↓
CLI or app passes options into run_download_job()
        ↓
run_download_job() loops through selected schools
        ↓
For each school, scrape relevant football stats/schedule pages
        ↓
Find game boxscore links
        ↓
Open each boxscore page
        ↓
Find downloadable play-by-play PDF
        ↓
Generate standardized filename
        ↓
Check:
    - already exists?
    - duplicate game?
    - valid link?
        ↓
Download if needed
        ↓
Create result record for each processed item
        ↓
Return all results
        ↓
Caller prints summary / stores job status / writes CSV
