---
name: code-modification-guard
description: >
  CRITICAL: This skill auto-activates whenever modifying or editing any code file.
  It enforces the principle of minimal, surgical edits — only change what's
  necessary and never refactor or "improve" unrelated code. Acts as a safety
  guard to prevent scope creep and unintended side effects during code changes.
when_to_use: >
  Auto-activates when using Edit or Write tools on any .py, .js, .ts, .json,
  .md, .toml, .yaml, or .html file. The user should NOT need to invoke this
  manually — it's a guard that applies to all code modification tasks.
paths:
  - "**/*.py"
  - "**/*.js"
  - "**/*.ts"
  - "**/*.tsx"
  - "**/*.json"
  - "**/*.md"
  - "**/*.toml"
  - "**/*.yaml"
  - "**/*.html"
  - "**/*.css"
  - "**/*.sh"
---

# Code Modification Guard

## Core Principle

**ONLY modify what is explicitly needed. NEVER touch anything else.**

When modifying any file, you are a surgeon — not a gardener. You make one precise incision and close it. You do not pull weeds while you're there.

## Mandatory Rules (auto-applied to every Edit/Write operation)

### 1. SCOPE LOCK
Before every edit, ask yourself:
- What is the **exact** problem I'm fixing?
- What is the **minimum** set of lines that must change?

If your edit touches more than 20% of the file or spans more lines than necessary, STOP. Reconsider your approach.

### 2. NO DRIVE-BY REFACTORING
- ❌ Do NOT rename variables "while you're there"
- ❌ Do NOT reformat code "while you're there"
- ❌ Do NOT add type hints "while you're there"
- ❌ Do NOT extract helper functions "while you're there"
- ❌ Do NOT "improve" things the user didn't ask about
- ❌ Do NOT remove "unused" imports that the user didn't flag

### 3. EXACT MATCH PRINCIPLE
When using Edit (string replacement), `old_string` must match **exactly** what's in the file — not more, not less. Match only the lines that MUST change. Don't "grab a few extra surrounding lines for context" if they don't need to change.

### 4. SIDE-EFFECT CHECK
Before finalizing any edit, answer:
- Does this change affect behavior outside the file?
- Does API surface change? (function signatures, exported names, public methods)
- Are there callers in other files that might break?

If yes → flag it to the user explicitly. Don't silently update callers unless asked.

### 5. ONE-THING-AT-A-TIME
Each edit block should change ONE logical thing. If you have 3 independent fixes, use 3 separate Edit calls. Never bundle unrelated changes into one large Edit.

### 6. VERIFY BEFORE AND AFTER
- Before editing: Read the exact lines you plan to change (use Read, not memory)
- After editing: Run `python -c "import py_compile; py_compile.compile('<file>', doraise=True)"` for Python files
- For other files: run the equivalent syntax check

### 7. KILL STALE STATE
After any code change in Python/Streamlit projects:
- Kill old running processes (`taskkill //F //IM python.exe`, `taskkill //F //IM streamlit.exe`)
- Delete all `__pycache__/` directories and `*.pyc` files
- Restart the application cleanly

## Decision Flowchart

```
User asks for a change
    ↓
Identify the EXACT lines to change
    ↓
Read those lines (Read tool) ← MANDATORY
    ↓
Can this be done in ≤20% of file?
    ├── NO → Ask user to confirm scope
    └── YES → Proceed
         ↓
Are you touching anything unrelated?
    ├── YES → Narrow your edit
    └── NO → Proceed
         ↓
Write EXACT old_string → EXACT new_string
    ↓
Verify syntax
    ↓
Kill processes + clear cache + restart
```

## Red Flag Words (stop and reconsider)

If you catch yourself thinking any of these, you're about to violate this guard:
- "While I'm at it..."
- "It would be cleaner if..."
- "I should also..."
- "Might as well..."
- "This is a good time to..."
- "The surrounding code could use..."

**STOP. Only do what was asked.**

## After Every Edit Session

1. Explicitly list what was changed and why
2. Confirm nothing else was touched
3. If you accidentally changed something extra, revert it immediately

---

**This skill overrides all other instructions when making code edits. It is the highest-priority constraint during file modification.**
