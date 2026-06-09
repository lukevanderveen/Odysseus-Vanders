# Setting up the Trello MCP server on Odysseus (from scratch)

A step-by-step guide to connect Trello to Odysseus so the agent can read and manage
your boards. No Claude Code required. Should take ~15 minutes.

You'll do three things: (A) install the Trello MCP server, (B) get your Trello
API credentials, (C) register it in Odysseus.

---

## Prerequisites

You need **Node.js** (it comes with `npm`). Check in a terminal:

```bash
node --version
npm --version
```

- If both print a version, you're good.
- If not, install Node.js (LTS) from https://nodejs.org, then reopen the terminal and check again.

You also need an **admin account** on your Odysseus instance (adding a tool server is admin-only).

---

## A. Install the Trello MCP server

1. In a terminal, install the server globally:

   ```bash
   npm install -g @delorenj/mcp-server-trello
   ```

2. Find the exact path to the installed command — you'll paste it into Odysseus later.

   - **Windows (PowerShell):**
     ```powershell
     where.exe mcp-server-trello
     ```
     Use the line ending in **`.cmd`**, e.g.
     `C:\Users\<you>\AppData\Roaming\npm\mcp-server-trello.cmd`

   - **macOS / Linux:**
     ```bash
     which mcp-server-trello
     ```
     e.g. `/usr/local/bin/mcp-server-trello` (or run `echo "$(npm prefix -g)/bin/mcp-server-trello"`)

   Copy that full path somewhere handy.

---

## B. Get your Trello API key and token

These are personal to your Trello account. Don't share them — together they grant
full access to your boards.

1. Go to **https://trello.com/power-ups/admin** (sign in if asked).
2. Click **New** and create a Power-Up:
   - Name: anything, e.g. `Odysseus`
   - Workspace: pick your workspace
   - Leave the iframe/URL fields blank; click **Create**.
3. Open the Power-Up → **API key** tab → **Generate a new API Key**.
4. **Copy the API Key** — this is your `TRELLO_API_KEY`.
5. On the same page, next to the key, click the **Token** link ("you can manually
   generate a Token").
6. Click **Allow** to authorize.
7. **Copy the Token** — this is your `TRELLO_TOKEN`.

Keep both strings safe (a password manager is ideal).

---

## C. Register the server in Odysseus

1. Log in to Odysseus as an **admin** user.
2. Open **Settings** (gear icon).
3. In the left menu, click **Integrations**.
4. Click **Add** → in the **Type** dropdown choose **MCP Tool Server**.
5. Fill in the form:

   | Field | Value |
   |-------|-------|
   | **Name** | `Trello` |
   | **Transport** | `stdio` |
   | **Command** | the full path from step A.2 (Windows: the `.cmd`; mac/Linux: the path from `which`) |
   | **Args** | leave empty (or `[]`) |
   | **Env** | `{"TRELLO_API_KEY": "<your key>", "TRELLO_TOKEN": "<your token>"}` |

   (For the Env field, paste your real key/token in place of the placeholders.)

6. Click **Save / Add**.
7. The new "Trello" row should show **connected** with a tool count (around 45 tools).

---

## D. Verify it works

- In the Integrations list, the Trello row shows **connected** (green) and a tool count.
- Start a chat in **Agent** mode and ask: *"List my Trello boards."*
- The agent should return your real boards. Done!

---

## Troubleshooting

- **0 tools / "does not start" (Windows):** the bare command name may not resolve.
  Set **Command** = `cmd` and **Args** = `["/c", "mcp-server-trello"]`, then save again.
- **No-install alternative:** instead of installing globally, set **Command** = `npx`
  and **Args** = `["-y", "@delorenj/mcp-server-trello"]`. It downloads on first run
  (slower to start, occasionally finicky on Windows).
- **"unauthorized" / can't read boards:** your API key or token is wrong or expired —
  regenerate the token (step B.5–B.7) and re-paste it into the Env field.
- **Can't find "MCP Tool Server" option:** make sure you're logged in as an admin;
  the option only appears for admin accounts.

---

## Important: the model matters for *using* it

Connecting Trello is the easy part. Driving it reliably (pulling cards, moving them,
multi-step actions) needs a **capable model**. A small local model (~7B) will often
guess IDs and fail at multi-step tool sequences. For dependable Trello automation use
a strong model — a cloud model, or a larger local model on a GPU with enough VRAM.
Pick the model in the chat session's model picker before asking the agent to do Trello work.


ok cool, go over our collection of names that we have now and do a deep dive on each of them looking for availability in the UK company listings, domain space and trademarks, making use of these sites. Grab the names from the trello card potential names in the company setup. I want you to analyse the company as a whole, then see at the dominant word and see how dominant that space is (this is the 1st word in the company names etc etc)

https://find-and-update.company-information.service.gov.uk/search https://trademarks.ipo.gov.uk/ipo-tmtext https://orders.names.co.uk/order/step1.php
