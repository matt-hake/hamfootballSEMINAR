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

## 1. Installation and Setup (you only need to do this the first time!)
        a. Visual Studio Code: Paste these links in your browser -> https://code.visualstudio.com/download 
        b. Python: https://www.python.org/downloads/
        c. Git: https://github.com/git-guides/install-git
        d. Click the downloads that correspond to your operating system (Windows, Mac, Linux)
        e. Open Visual Studio Code
        f. Hit the following keys, For Mac: Cmd + Shift + \`    For Windows: Ctrl + Shift + \`
        g. In the window that just opened copy and paste the following lines and hit enter after each one
        ```
                git clone https://github.com/hamfootballSEMINAR/hamfootballSEMINAR.git
                cd hamfootballSEMINAR
                python3 -m venv .venv
                source .venv/bin/activate
                pip install -r requirements.txt
                pip install fastapi "uvicorn[standard]" jinja2 pydantic requests beautifulsoup4 lxml flask
        ```
        *For this next line, paste your api key where it says your_actual_api_key_here, don't delete the quotation marks*
        ```
                echo 'GROQ_API_KEY="your_actual_api_key_here"' > .env
        ```

## 2. Running the Code (do this when you want to run it in the future)
        a. Open the Virtual Studio Code Application
        b. Navigate to the hamfootballSEMINAR/src directory
                i. Mac: Cmd + o     Windows: Ctrl + o
                ii. Find and select the folder named hamfootballSEMINAR
        c. In the terminal (Mac: Cmd + Shift + \` Windows: Ctrl + Shift + \`) paste the following and hit enter after each one
                Mac: ```source .venv/bin/activate```  Windows: ```source .venv\Scripts\activate```  
                ```python3 -m flask --app src/app/api.py --debug run```
        d. Look at the line above the yellow text, there will be a line that says something similar to  
        "\* Running on http://127.0.0.1:5000", hover over this link and click "follow link" when it appears (or hover over it and do Mac: cmd + click   Windows: ctrl + click)

## 3. Navigating the UI
        a. At the top of the page, select the years you would like scrape.
        b. Select the school(s) you are interested in
        c. click "find games", the scraper will now find all requested games (this may take 10-20 seconds)
        d. Under "Discovered Games", find the game(s) you are interested in and click "Parse Game" (this may take a few minutes)
        WARNING: Do not try to parse multiple games at once!
        e. You can then review the spreadsheet in the browser or click "Download Excel" to make edits
        f. Notice the first column of the spreadsheet "HUMAN VERIFIED", once a row is verified, you can click on the cell in the first column and click "yes".
        g. Once you are finished click back into the terminal and hit ctrl + c to close the vitrual environment


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
