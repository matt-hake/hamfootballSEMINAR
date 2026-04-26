# NESCAC Football PDF Pipeline

This project provides a **web-based interface** for running a Python data pipeline that:
1. Scrapes football play-by-play PDFs from NESCAC athletics sites  
2. Converts play-by-play PDF into an Excel file
3. Allows user to download the results 

---

## Prerequisites

1. macOS or Windows device
2. Internet connection (pipeline makes live HTTP requests)

---

## 1. Installation and Setup (you only need to do this the first time!)

**For Mac:**

a. Visual Studio Code: Paste this link in your browser -> https://code.visualstudio.com/download 

b. Click the download that corresponds to your operating system (Mac)

c. Open Visual Studio Code

d. Hit the following keys, Cmd + Shift + ` 

e. In the window that just opened, copy and paste the line below and hit enter. If prompted to install, click "install".

```bash
git    
```                

f. Once installation finishes, paste the entire block of code below (all together, not one line at a time) into the same window and hit enter. When prompted, paste your api key into the window and hit enter.

```bash
git clone https://github.com/hamfootballSEMINAR/hamfootballSEMINAR.git && \ cd hamfootballSEMINAR && \
python3 -m venv .venv && \
source .venv/bin/activate && \
pip install --upgrade pip && \
pip install -r requirements.txt && \
pip install fastapi "uvicorn[standard]" jinja2 pydantic requests beautifulsoup4 lxml flask && \
echo "" && \
echo "---------- SETUP ALMOST COMPLETE ----------" && \
read -p "Paste your GROQ_API_KEY here and press Enter: " apikey && \
echo "GROQ_API_KEY=\"$apikey\"" > .env && \
echo "Success! you are now setup to run the program."
```

g. You are ready to move on to step 2 (Running the Code)

**For Windows:**

a. Visual Studio Code: Paste this link in your browser -> https://code.visualstudio.com/download 

b. Click the download that corresponds to your operating system (Windows)

c. Open Visual Studio Code

d. Hit the following keys, Ctrl + Shift + `

e. In the window that just opened, copy and paste the line below and hit enter.

```bash
python
```

f. If Microsoft Store opened, click "get" or "install"

g. copy and paste the line below and hit enter (in the same window from step e)

```bash
winget install --id Git.Git -e --source winget
```

h. A progress bar will appear in the terminal. When prompted "Do you agree to all the source agreements terms?" type "Y" and hit enter.

i. Quit VSCode and reopen it 

j. Hit the following keys, Ctrl + Shift + `

k. Paste the entire block of code below (all together, not one line at a time) into the same window and hit enter. When prompted, paste your api key into the window and hit enter.

```bash
git clone https://github.com/hamfootballSEMINAR/hamfootballSEMINAR.git; `
cd hamfootballSEMINAR; `
python -m venv .venv; `
.\.venv\Scripts\python.exe -m pip install --upgrade pip; `
.\.venv\Scripts\python.exe -m pip install -r requirements.txt; `
.\.venv\Scripts\python.exe -m pip install fastapi "uvicorn[standard]" jinja2 pydantic requests beautifulsoup4 lxml flask; `
Write-Host "`n---------- SETUP ALMOST COMPLETE ----------" -ForegroundColor Cyan; `
$apikey = Read-Host "Paste your GROQ_API_KEY here"; `
"GROQ_API_KEY=`"$apikey`"" | Out-File -FilePath .env -Encoding ascii; `
Write-Host "Success! you are now setup to run the program." -ForegroundColor Green
```

l. You are ready to move on to step 2 (Running the Code)

## 2. Running the Code (do this when you want to run it in the future)

**For Mac:**

a. Open the Virtual Studio Code Application

b. Navigate to the hamfootballSEMINAR/src directory (you may already be in it)

i. Cmd + o 

ii. Find and select the folder named hamfootballSEMINAR

c. In the terminal (Cmd + Shift + `) copy and paste the line below and hit enter

```bash
source .venv/bin/activate && python3 -m flask --app src/app/api.py --debug run
```

d. Look at the line above the yellow text, there will be a line that says something similar to  
"* Running on http://127.0.0.1:5000", hover over this link and click "follow link" when it appears (or hover over it and do cmd + click)

**For Windows:**

a. Open the Virtual Studio Code Application

b. Navigate to the hamfootballSEMINAR/src directory (you may already be in it)

i. Ctrl + o

ii. Find and select the folder named hamfootballSEMINAR

c. In the terminal (Ctrl + Shift + `) copy and paste the line below and hit enter

```bash
.\.venv\Scripts\activate; python -m flask --app src/app/api.py --debug run
```

d. Look at the line above the yellow text, there will be a line that says something similar to  
"* Running on http://127.0.0.1:5000", hover over this link and click "follow link" when it appears (or hover over it and do ctrl + click)

## 3. Navigating the UI

a. At the top of the page, select the years you would like scrape.
<img width="1438" height="732" alt="Screenshot 2026-04-25 at 9 57 37 AM" src="https://github.com/user-attachments/assets/b164c77f-061a-4a7e-b540-5ee0dd767723" />

b. Select the school(s) you are interested in and click "find games", the scraper will now find all requested games (this may take 10-20 seconds)
<img width="1438" height="732" alt="Screenshot 2026-04-25 at 9 57 37 AM" src="https://github.com/user-attachments/assets/de511a8f-10ec-4af1-9dcc-b63512122259" />

c. Scroll down, Under "Discovered Games", find the game(s) you are interested in and click "Parse Game" (this may take a few minutes)
**WARNING: Do not try to parse multiple games at once!**
<img width="1388" height="713" alt="Screenshot 2026-04-26 at 11 28 12 AM" src="https://github.com/user-attachments/assets/50ad90fe-0221-4df4-80a6-e7c2e231a11c" />


d. You can then review the spreadsheet in the browser by clicking "HITL Review" (human in the loop review). 
<img width="1421" height="711" alt="Screenshot 2026-04-26 at 11 33 35 AM" src="https://github.com/user-attachments/assets/841a60e1-aef1-4010-b045-e0ada76754be" />

<img width="1430" height="726" alt="Screenshot 2026-04-26 at 11 35 45 AM" src="https://github.com/user-attachments/assets/1f2b29e2-2ac6-4b2b-ad2c-e57ee24ffbc3" />

e. Once you've finished reviewing, exit review (as demonstrated in the picture above). You can then download the excel file by clicking "Download Excel".
<img width="1426" height="768" alt="Screenshot 2026-04-26 at 11 48 03 AM" src="https://github.com/user-attachments/assets/d3f164e8-5712-4c85-b107-6729b77aac2c" />


f. **Once you are finished click back into the terminal (in VSCode) and hit ctrl + c to close the vitrual environment**
