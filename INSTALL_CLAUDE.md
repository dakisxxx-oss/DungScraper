# Install DungScraper for Claude Desktop on Windows

## Trumpa instrukcija (lietuviškai)

1. Atidarykite **PowerShell**. Klonuokite repozitoriją į namų aplanką:

   ```powershell
   git clone https://github.com/darius-ra-auskas/DungScraper.git $env:USERPROFILE\DungScraper
   cd $env:USERPROFILE\DungScraper
   ```

2. Sukurkite virtualią aplinką ir įdiekite (reikia Python 3.12):

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -e ".[stealth]"
   ```

   Jei `stealth` priedas neįdiegiamas, naudokite paprastą diegimą: `pip install -e .`

3. Atsisiųskite naršyklę: `python -m patchright install chromium`. Jei Patchright nėra, `python -m playwright install chromium`.

4. Prijunkite prie Claude: `dungscraper install-claude`. Komanda pati pasirenka Microsoft Store arba klasikinį konfigūracijos failą ir padaro atsarginę kopiją. Claude neperkraunama.

5. Visiškai uždarykite Claude iš sistemos dėklo (tray): dešiniu pelės mygtuku ant piktogramos, **Quit**. Tada atidarykite Claude iš naujo.

6. Patikrinkite pokalbyje: „Use DungScraper to scrape https://example.com and show the markdown.“

Reddit veikia be raktų. Pavyzdys: „Use the DungScraper reddit tool to list hot posts in r/Python.“ Jei Reddit atsako `reddit_unavailable` arba `reddit_rate_limited`, dažniausiai kaltas duomenų centro IP arba per dažnos užklausos. Namų ryšys paprastai veikia. Byparr, jei reikia, aprašytas angliškoje dalyje.

### Kas naujo

- **Reddit be API.** Įrankis `reddit` ir paprastas `scrape` su reddit.com adresu. Eilė: viešas JSON, tada RSS, tada old.reddit HTML. Oficialūs raktai naudojami tik jei juos patys įrašote, ir nebėra privalomi. Reddit nebepatvirtina paprastų programų (reikia Devvit), todėl raktų laukti neverta.
- **`read_feed`.** RSS/Atom (tinklaraščiai, naujienos) kaip sąrašas: pavadinimas, nuoroda, autorius, data, santrauka. `scrape` tą patį srautą irgi atpažįsta.
- **`outline`.** Kai `scrape` apkarpomas, atsakyme yra antraštės ir `offset`, kad kitą kartą būtų galima šokti į tą vietą.
- **Crawl-delay.** robots.txt pauzė gerbiama, bet ne ilgiau kaip 10 sekundžių.
- **YouTube titrai.** Jei senasis caption sąrašas tuščias, titrai imami iš paties vaizdo puslapio.

## English instructions

Follow these steps in order. They are written so a person or Claude Desktop can run them. Python 3.12 is required. The commands below are PowerShell.

### 1. Clone into your user profile

```powershell
git clone https://github.com/darius-ra-auskas/DungScraper.git $env:USERPROFILE\DungScraper
cd $env:USERPROFILE\DungScraper
```

That folder is `C:\Users\YOUR_NAME\DungScraper`. Replace `YOUR_NAME` everywhere below with the name from `$env:USERNAME`.

### 2. Create a virtual environment and install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[stealth]"
```

The quotes around `".[stealth]"` are required. That extra installs curl_cffi, Patchright, Camoufox, and SeleniumBase.

If that extra fails to build, install the base package instead. Scraping, the MCP server, and `install-claude` still work. The heavier anti-bot tiers are skipped until the extra is installed.

```powershell
pip install -e .
```

### 3. Install the browser

With the stealth extra:

```powershell
python -m patchright install chromium
```

With the plain install, or if the command above is missing:

```powershell
python -m playwright install chromium
```

Use the venv Python (`.\.venv\Scripts\python.exe`) so the browser is registered for this environment. This step needs a network connection once. After it finishes, scrapes can run offline except for the pages you fetch.

### 4. Register the MCP server

**Preferred:** from the activated venv, run:

```powershell
dungscraper install-claude
```

This finds the config file, copies the old one to `claude_desktop_config.json.bak-<timestamp>`, and adds the `dungscraper` server. It does not start or restart Claude. Other MCP servers already in the file are left as they are.

It looks for the Microsoft Store (MSIX) package first. If `%LOCALAPPDATA%\Packages\Claude_*` exists, it writes:

`%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude_desktop_config.json`

If several `Claude_*` folders exist, the one that already has a config file is used. If no Store package folder exists, it writes the classic file:

`%APPDATA%\Claude\claude_desktop_config.json`

**Or add the entry yourself.** Claude does not expand environment variables inside `command`, so use a full path. After the venv install, `install-claude` writes the first form when `dungscraper.exe` is on `PATH`:

```json
{
  "mcpServers": {
    "dungscraper": {
      "command": "C:\\Users\\YOUR_NAME\\DungScraper\\.venv\\Scripts\\dungscraper.exe",
      "args": ["mcp"],
      "env": {}
    }
  }
}
```

If you would rather pin the venv interpreter, this is equivalent:

```json
{
  "mcpServers": {
    "dungscraper": {
      "command": "C:\\Users\\YOUR_NAME\\DungScraper\\.venv\\Scripts\\python.exe",
      "args": ["-m", "dungscraper", "mcp"],
      "env": {}
    }
  }
}
```

Create the `Claude` folder and the JSON file if they are not there yet. If `mcpServers` already exists, add only the `dungscraper` key. Keep the file valid JSON.

### 5. Quit Claude from the tray, then reopen

Closing the window is not enough. Claude Desktop keeps running in the system tray and will not load a new MCP server until that process exits.

1. Click the Claude icon in the notification area (the system tray). You may need **Show hidden icons**.
2. Right-click it and choose **Quit**.
3. Start Claude Desktop again when you are ready.

`install-claude` will not do this for you.

### 6. Reddit needs no API keys

Subreddit listings (`hot`, `new`, `top`), a post and its comments, search, and user posts work with an empty `env` block. DungScraper tries public JSON (`old.reddit.com`, then `www.reddit.com`, then `np.reddit.com`), then RSS, then old.reddit HTML. Requests are spaced (about 1.25 seconds). A `429` waits and retries once. If Reddit asks for a long wait, the tool returns `reddit_rate_limited` instead of hammering the site.

Ask Claude: use the DungScraper reddit tool to list hot posts in r/Python, with author and score.

A datacenter IP is often blocked (`reddit_unavailable`). A home connection usually gets JSON. Private subreddits return `reddit_private`. Nothing in this path says Reddit is "not configured".

Keys are optional. Reddit no longer approves ordinary script apps (new apps are Devvit), so these variables will usually stay empty. If you already have a client id and secret, put them in the server `env` block. They are used first, and the public chain is only the fallback when that API fails:

```json
{
  "mcpServers": {
    "dungscraper": {
      "command": "C:\\Users\\YOUR_NAME\\DungScraper\\.venv\\Scripts\\dungscraper.exe",
      "args": ["mcp"],
      "env": {
        "DUNGSCRAPER_REDDIT_CLIENT_ID": "paste_the_id_under_the_app_name",
        "DUNGSCRAPER_REDDIT_CLIENT_SECRET": "paste_the_secret",
        "DUNGSCRAPER_REDDIT_USERNAME": "your_reddit_username",
        "DUNGSCRAPER_REDDIT_PASSWORD": "your_reddit_password",
        "DUNGSCRAPER_REDDIT_USER_AGENT": "dungscraper/0.1 by yourname"
      }
    }
  }
}
```

The user agent must be a unique string, not a browser copy. After editing the file, quit Claude from the tray and reopen it. Leaving the variables unset is the normal setup.

### 7. Optional: Byparr sidecar

Byparr speaks the same local API as FlareSolverr and can mint Cloudflare clearance cookies. It is not required. From the repo folder, with Docker installed:

```powershell
docker compose --profile antibot up
```

The sidecar listens on port 8191. Add this to the same `env` block as above:

```json
"DUNGSCRAPER_FLARESOLVERR_URL": "http://127.0.0.1:8191/v1"
```

Leave it unset to skip that tier. No free tool in this stack reliably solves an interactive image captcha. Clearance cookies are tied to the IP that created them, so a home connection works more often than a VPS. Some sites' terms forbid automated access. `robots.txt` is still respected by default.

### 8. Verify inside Claude

Open a new chat. Ask Claude to use the DungScraper tools. Examples:

- Use DungScraper to scrape https://example.com and show the markdown title and the first paragraph.
- Use DungScraper map_site on https://docs.python.org/3/ with search "tutorial" and list 10 URLs.
- Use DungScraper to scrape https://en.wikipedia.org/wiki/Web_scraping and quote the first heading.

A working server answers with page text, not a message that the tool is missing. In Claude Desktop, the tool name appears as DungScraper. The first call can take longer while the browser starts.

Try: Use the DungScraper reddit tool to list the current hot posts in r/Python, with author and score. No Reddit keys are required.

### Troubleshooting

| What you see | What to check |
| --- | --- |
| DungScraper is not in the tool list | Quit from the tray, not just the window. Reopen Claude. Confirm the JSON file you edited is the one Claude is reading (Store path vs `%APPDATA%\Claude`). |
| `dungscraper` is not recognized | The venv is not active, or `command` is not an absolute path. Use the `dungscraper.exe` or `python.exe` snippet in step 4. |
| JSON error on startup | A trailing comma or a missing brace in `claude_desktop_config.json`. The backup next to it is `claude_desktop_config.json.bak-<timestamp>`. |
| Tool runs, scrape fails immediately | In the venv run `python -m playwright install chromium` or `python -m patchright install chromium`. |
| Reddit returns `reddit_unavailable` | This IP is blocked. A home connection usually works. The tool already tried JSON, RSS, and old.reddit HTML. |
| Reddit returns `reddit_rate_limited` | Wait and call again. The message includes `Retry-After` when Reddit sent one. |
| Still a "Just a moment" page | The site blocked this IP. Optional next steps are the stealth extra, a home IP, or Byparr. Image captchas are not solved. |

Logs:

- Classic install: `%APPDATA%\Claude\logs`
- Microsoft Store install: `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\logs`

Look at the newest `mcp*.log` file there if the server fails to start. The DungScraper process itself also prints to that MCP log.
