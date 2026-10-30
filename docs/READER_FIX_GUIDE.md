# When a questionnaire is read wrong — where to look and how to fix it

This is the map for fixing reading problems yourself. Most problems can be fixed
**in the app, with no code**; the rest are fixed in one file with one test and
one measurement. Nothing here needs you to guess.

---

## 1. How a document is read (and which file does each step)

```
.docx ──► 1 LOAD ──► 2 LABELS ──► 3 SPLIT ──► 4 READ EACH QUESTION ──► 5 WHOLE-DOCUMENT PASSES ──► review page / link
```

| Step | What happens | File · function |
|---|---|---|
| 1 Load | Word paragraphs and tables in order; struck-out and deleted text dropped; tabs kept | `src/intake/questionnaire_loader.py` · `load_docx`; `src/intake/revisions.py` · `live_text` |
| 1 Load — question cards | A document that puts each question in its own table (name / label / wording, codes \| answers, notes) is rewritten into plain lines | `src/intake/card_tables.py` · `card_lines`, `label_of` |
| 2 Labels | "Q1." alone on a line joined to its wording; a translated second question line kept as a note; "1." number labels accepted when they run in sequence | `questionnaire_loader.py` · `_normalise_labels` |
| 2 Labels — taught | Labels an owner taught by example ("QGENDER.", "Question 5:") | `src/intake/taught_labels.py` |
| 3 Split | The document cut into one piece per question; information screens become INTRO / INFO_n; a TERMINATE line stays with its question | `questionnaire_loader.py` · `segment_into_questions`, `_QID_RE`, `_is_screen` |
| 4 Read | Wording, type, answers and codes, tags ([EXCLUSIVE], [TERMINATE]), routing, randomise — from the text itself | `src/engines/questionnaire/prose_directives.py` · `parse_prose_question`, `_TYPE_PATTERNS` |
| 4 Read — spec tables | "Q Name / Q type / Response list" tables (GBK style) | `src/engines/questionnaire/spec_table.py` |
| 4 Read — grids | Statements × scale tables, including punch-code grids | `src/engines/questionnaire/table_questions.py` · `grid`, `_punch_codes` |
| 4 Read — model | ONLY what the text did not state goes to a model; if none is reachable the question is flagged, never guessed | `src/engines/questionnaire/extractor.py` · `extract_question` |
| 5 Passes | Section bases, loops, list sources, hidden variables, the study brief and its checklist, the "How this questionnaire was read" report | `extractor.py` · `extract_survey_definition`; `section_paths.py`, `study_overview.py`, `brief_checklist.py`, `decoding_report.py` |
| Conditions people write | "QAGE < 18", "S2 = 1, 2" → a rule the link runs | `src/engines/questionnaire/conditions.py` |
| Notes read like an analyst | variables the notes say to create (rules compiled where exact), questions only a person can settle, quality checks — "What a reader would ask" on the review page | `src/engines/questionnaire/reading_questions.py` · `analyst_reading` |

## 2. First, find WHERE it went wrong (2 minutes)

1. **Review page → "How this questionnaire was read"** (top of the page). It says
   which reading skills worked, which questions none could read, what the document
   asks for and whether each need is met.
2. **Ask the Copilot**: *"the questions in this project look wrong — check how it was
   read"*. It runs the diagnosis: how the document marks its questions, how many the
   reader finds today against how many the project holds.
3. **On your machine**, see exactly how the document was split:

```bash
cd ~/dev/dvv-research-cloud
BEDROCK_API_KEY= .venv/bin/python -c "
from src.intake.questionnaire_loader import load_docx, segment_into_questions
for c in segment_into_questions(load_docx('PATH/TO/file.docx')):
    print('=====', c.candidate_qid); print(c.chunk_text[:300])"
```

If a question is missing or two are glued together → the problem is in steps 1–3.
If the pieces are right but a question has the wrong type/answers → step 4.

## 3. Symptom → fix

| What you see | Fix in the app (no code) | Fix in code | Prove it |
|---|---|---|---|
| Questions not detected, or many glued into one | Copilot: give one label as written → **Teach** (kind `question_label`) | `_QID_RE` or `_normalise_labels` in `questionnaire_loader.py` | `scripts/reader_whatif.py` + eval |
| One table per question, read as nothing | — | `card_tables.py` (`label_of` decides what is a card) | `tests/unit/test_card_tables.py` |
| A line that is not an answer shows as one (translation, note, heading) | Edit the question (Copilot or editor) | `_normalise_labels`; card heads in `card_lines` | eval: no newly wrong |
| Wrong type (single vs multi, grid…) | Correction box → "Teach the reader" (`type_signal`) | `_TYPE_PATTERNS` in `prose_directives.py` | add a case to `tests/unit/test_prose_directives.py` |
| A tag on an answer not understood ("[KILL]") | Teach `option_tag` | option tags in `prose_directives.py` | same |
| Grid with no rows/columns, or wrong codes | Edit the question | `table_questions.py` · `grid`, `_punch_codes`, `_interior_is_empty` | `tests/unit/test_table_questions.py` |
| Screen-out / routing missing | Copilot: "terminate QAGE if QAGE < 18" → card | `routing_prose.py`; TERMINATE lines in `segment_into_questions` | eval |
| Hidden variable read as a question (or the reverse) | Teach `hidden_variable`; Copilot: "create a hidden variable…" | `is_derived_wording` in `prose_directives.py` | eval |
| The cover page / brief shows as a question | — | `study_overview.looks_like_overview` | eval |
| A need in the brief is not checked | — | `brief_checklist.py` | `tests/unit/test_brief_checklist.py` |
| The Copilot understands but cannot DO it | — | add a `propose_*` tool: `api/routes/copilot.py` (`make_executor`), its description in `src/ai/copilot.py` (`TOOLS`), its area in `src/ai/copilot_router.py` | a case in `evals/copilot/cases.json` |
| The Copilot answers "I could not look that up" | Retry; flag it with **Wrong answer?** | `_asserts_data` / `copilot_verify.untraced` in `src/ai/copilot.py` | `tests/unit/test_copilot_grounding.py` |

**Teach before you code.** A taught convention applies to every later document
written that way, costs no model call, and can be switched off in the format's
rules. Code is for a *shape* the reader has never seen.

## 4. The fix loop — every change, same five steps

1. **Reproduce** it in a test with a few lines that copy the document's shape
   (never the client's words). Tests live in `tests/unit/`.
2. **Fix** it in the one function the table above names.
3. **Measure** on everything with a known correct reading — AI off, so only the
   reader is measured:

```bash
export BEDROCK_API_KEY= GROQ_API_KEY= CEREBRAS_API_KEY= HF_TOKEN= OLLAMA_HOST=http://127.0.0.1:1
python scripts/eval_compare.py --batch ~/dev/DVV/eval/gold ~/dev/DVV/eval/synthetic/dev \
    ~/dev/DVV/eval/synthetic/holdout --out ~/dev/DVV/eval/rounds/my-fix
python scripts/eval_diff.py <last-round> my-fix        # must say: newly wrong: none
python scripts/reader_whatif.py ~/dev/DVV/eval/synthetic/dev/*.docx   # every style 100%
```

4. **Test everything**: `pytest tests/unit tests/integration` and `ruff check src api tests scripts`.
5. **Record** it at the top of `docs/CHANGE_TRACKER.md` with the numbers, then a PR.

## 5. Where things live

| | |
|---|---|
| Real questionnaires + their correct readings | `~/dev/DVV/eval/gold/*.json` (each names its .docx). **Never committed.** |
| Synthetic questionnaires (safe to share) | `~/dev/DVV/eval/synthetic/{dev,holdout}` |
| Measurement rounds | `~/dev/DVV/eval/rounds/<name>/` (`summary.md`, per-document `report.md`) |
| Copilot test cases | `evals/copilot/cases.json` → `./dvv eval` on the server |
| What changed and what it measured | `docs/CHANGE_TRACKER.md` |

Adding a real document to the measured set: write its correct reading as
`~/dev/DVV/eval/gold/<name>.json` (format: `~/dev/DVV/eval/GOLD_SCHEMA.md`). From
then on every change is measured against it too — that is how the reader stops
breaking things it already read.
