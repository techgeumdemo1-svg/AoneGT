# INSTRUCTION.md
# Auto-Sync Rule — Project Source of Truth

This instruction applies to ALL AI models, agents, CLI tools, 
and IDE assistants working in this project.
This is a standing order — it never expires, it applies to 
every single session, every single task, without exception.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RULE — AFTER EVERY TASK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
After completing ANY task or process in this project:

STEP 1 — DETECT CHANGES
Check if any .py file or related config/schema file 
was created, modified, or deleted during this session.
Skip migration files.

STEP 2 — IF NO CHANGES DETECTED
Do nothing. Task is complete.

STEP 3 — IF ANY CHANGES ARE DETECTED
Do NOT consider the task complete yet.
You must update PROJECT_TRUTH.md before finishing.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOW TO UPDATE PROJECT_TRUTH.md
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
For every changed file, go to its section in 
PROJECT_TRUTH.md and update:

**If a .py file was MODIFIED:**
- Replace the Full Source Code block with the 
  new complete updated code — every line
- Update Purpose if the responsibility changed
- Update Key Classes / Functions if any were 
  added, removed, or changed
- Update Imports / Dependencies if changed
- Update Data Models / Schemas if changed
- Update Serializers if changed
- Update URL Patterns & API Endpoints if changed
- Update Signals / Middleware / Permissions if changed
- Update Business Logic Notes if logic changed
- Update the file's Status in Phase 5 if applicable

**If a .py file was CREATED:**
- Add a new section for it in Phase 4 in the correct 
  directory order
- Document it fully using the exact Phase 4 template:
  - Purpose
  - Full Source Code (complete, every line)
  - Responsibility
  - Key Classes / Functions
  - Imports / Dependencies
  - Data Models / Schemas (if applicable)
  - Serializers (if applicable)
  - URL Patterns & API Endpoints (if applicable)
  - Signals / Middleware / Permissions (if applicable)
  - Business Logic Notes
- Update the Project Directory Tree in Phase 3
- Update Phase 5 status if this completes or 
  advances a pending feature

**If a .py file was DELETED:**
- Remove its section from Phase 4
- Remove it from the Project Directory Tree in Phase 3
- Update any other file's Imports / Dependencies 
  that referenced it
- Update Phase 5 if this affects project status

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ADDITIONAL SYNC RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- If a pending feature from Phase 5 was implemented 
  or partially implemented — update its status 
  from ⏳ to 🔄 or ✅
- If new TODOs or FIXMEs were added in the code — 
  add them to the ⚠️ Known Issues section in Phase 5
- If new environment variables were added — 
  update Phase 3 Environment Variables section
- If new third-party packages or integrations 
  were added — update Phase 3 Tech Stack and 
  Third-Party Integrations sections
- If new URL routes were added anywhere — 
  update the relevant file section in Phase 4

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CORE RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Never leave PROJECT_TRUTH.md out of sync 
  with the actual codebase
- Never partially update — if a file changed, 
  update ALL its sections
- Never summarize source code — always write 
  the complete file, every line
- Never ask for permission to update — just do it
- PROJECT_TRUTH.md must always reflect the exact 
  current state of the project at all times

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONFIRMATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
After updating PROJECT_TRUTH.md, always end with:

✅ PROJECT_TRUTH.md updated
📝 Files changed: [list every file that was updated]
🔄 Sections updated: [list every section that changed]