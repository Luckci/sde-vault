# 📚 SDE Vault

**SDE Vault** downloads your courses from [learn.sde.dk](https://learn.sde.dk) (Moodle) and turns them into a beautifully structured [Obsidian](https://obsidian.md) vault — built for exam preparation.

Instead of digging through Moodle's clunky interface every time you want to study, SDE Vault pulls everything down once: notes, files, assignments, and quizzes — organised into clean Markdown with working links, images, and an exam dashboard. Optionally, it generates AI study notes, flashcards, and practice exams per topic using Claude, ChatGPT, or Gemini.

---

## Download

Go to the [**Releases page**](https://github.com/Luckci/sde-vault/releases) and download the file for your platform:

| Platform | File |
|---|---|
| **Windows** | `SDE-Vault-Windows.exe` — double-click to run |
| **macOS** | `SDE-Vault-Mac.zip` — unzip, then double-click `SDE-Vault` |

No Python or installation required. The app opens a browser window at `http://localhost:5000` automatically.

> **macOS only:** On first launch you may see *"cannot be opened because the developer cannot be verified"*. Right-click the binary → Open → Open anyway.

> **Chrome required:** SDE Vault uses Chrome to log in to Moodle. Make sure Google Chrome is installed.

---

## Getting started

1. Download and run the app for your platform (see above)
2. A browser tab opens at `http://localhost:5000`
3. Enter your **school email** (e.g. `madsb@otg.dk`) and **password** — saved locally, never sent anywhere else
4. Pick a subject (e.g. *Fysik A*), optionally a specific topic, and click **Start download**
5. Your vault appears in a `vault/` folder next to the app — open it in Obsidian

---

## Features

### Core download

| Option | What it does |
|---|---|
| **Single subject** | Download one course (e.g. *Kemi B*) |
| **All courses** | Download your entire account in one run |
| **Single topic** | Download just one section within a course |
| **Resume** | Skip sections whose notes already exist — useful after an interruption |
| **Sync** | Re-fetch only sections whose content has changed since last run |
| **Show browser** | Open a visible Chrome window — required on school WiFi where login needs interaction |
| **Universal export** | Skip the `.obsidian/` folder — output is plain Markdown compatible with Logseq, Typora, VS Code + Foam, Zettlr, etc. |

### AI features

All AI features are optional and require an API key. Add it in the settings panel inside the app.

| Feature | What it generates |
|---|---|
| **AI study notes** | Key concepts, formulas, exam traps, and a revision checklist per topic |
| **Flashcards** | Obsidian Spaced Repetition format — open and review immediately |
| **Practice exam** | 5–8 exam-style questions with hints and collapsible model answers |
| **Cross-subject concept map** | Finds shared concepts across all subjects in your vault (requires ≥ 2 subjects) |

**Supported AI providers:**

| Provider | Default model | Approx. cost per course (~14 topics) |
|---|---|---|
| Claude (Anthropic) | `claude-haiku-4-5` | ~$0.10 |
| ChatGPT (OpenAI) | `gpt-4o-mini` | ~$0.02 |
| Gemini (Google) | `gemini-2.0-flash` | ~$0.01 |

You can override the model in the UI if you want higher quality output.

### Exam tools

Every downloaded course also gets:

- **`📋 Eksamensforberedelse.md`** — topic status table, assignment checklist, formula file index, and a Dataview query for auto-updating status
- **`📅 Afleveringsfrister.md`** — all assignments with their due dates in a sortable table
- **`Home.md`** — master overview linking all subjects and topics

---

## Vault structure

```
vault/
├── Home.md                            ← master overview of all subjects
├── 🔗 Begrebskort.md                  ← cross-subject concept map (AI)
│
├── Fysik_A/
│   ├── _Index.md                      ← subject overview + topic links
│   ├── 📋 Eksamensforberedelse.md     ← exam dashboard
│   ├── 📅 Afleveringsfrister.md       ← assignment deadlines
│   │
│   └── Energi/
│       ├── Energi.md                  ← section notes, materials, links
│       ├── Energi — AI Studienoter.md ← AI study notes (optional)
│       ├── Energi — Flashcards.md     ← spaced repetition cards (optional)
│       ├── Energi — Prøveeksamen.md   ← practice exam (optional)
│       ├── _images/                   ← embedded images (auto-downloaded)
│       └── (downloaded files)
│
└── Kemi_B/
    └── …
```

---

## Update notifications

The app checks GitHub for new releases every time it starts. If a newer version is available, a banner appears at the top of the page with a direct download link. You can always find the latest release at [github.com/Luckci/sde-vault/releases](https://github.com/Luckci/sde-vault/releases).

---

## Running from source

If you prefer to run from source rather than the pre-built binary:

```bash
git clone https://github.com/Luckci/sde-vault.git
cd sde-vault
pip install -r requirements.txt
python launch.py
```

Requires Python 3.10+ and Google Chrome.

---

## Privacy & security

- Credentials are stored in a `.env` file **next to the app on your own machine** — they are never uploaded or transmitted anywhere except to the school's login page (`login.microsoftonline.com` / `adfs.sde.dk`)
- The app runs entirely locally — no cloud backend, no telemetry
- API keys (for AI features) are also stored in the same local `.env` file

---

## Limitations

- Only works with courses on **learn.sde.dk** (SDE's Moodle instance)
- Only downloads content you are **already enrolled in**
- Some Moodle activity types (video embeds, H5P, BigBlueButton) are noted as links but their content cannot be downloaded
- AI-generated content is in Danish by default — the prompts are tuned for Danish gymnasium level
