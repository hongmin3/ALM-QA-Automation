# ALM-QA-Automation integration plan

Goal: one local project and one primary GitHub repository, retaining both applications and their complete Git ancestry.

Approved direction: ALM-QA-Automation; separate apps/srs-spec and apps/issue-export, shared launcher. User requested implementation on 2026-09-21.

## Execution

1. Preserve original workspaces including uncommitted work and ignored operational data. Create independent Git clone and history bundles. Never inspect operational configuration or internal implementation logs.
2. Move tracked SRS application files under apps/srs-spec; keep Akela context at root. Merge the issue repository with both parents retained, placing its tree under apps/issue-export without rewriting original commits.
3. Copy current working files and opaque operational files to matching application directories. Preserve source bytes; exclude caches and nested Git databases. Ignore private artifacts.
4. Add a Python dispatcher and Windows menu. Execute each application in its own directory, forward arguments unchanged and propagate exit codes. Keep separate configuration formats.
5. Test dispatcher routing, argument preservation, errors and invocation from another directory. Run existing SRS suite and safe CLI help checks; do not invoke live collection, publication or email.
6. Compare source and data preservation, inspect staged file list and diff, verify both historical tips are ancestors. Commit integration.
7. Rename primary GitHub repository and push only after local checks. Keep former issue repository as historical backup.
8. Back up scheduled task definitions, preserve task names/triggers/settings and update only SRS action working directory. Verify persisted paths. Keep original folders for rollback.

## Risks and boundaries

- Both repositories contain uncommitted edits; originals must remain untouched.
- Operational configs, generated documents and ALM data must never enter Git.
- No changes to SRS src code. Knowledge is moved unchanged; report path changes through Akela instead of editing wiki.
- Absolute paths inside protected configs are not inspected; live ALM execution is outside migration verification.
- Existing task names stay stable to avoid duplicate schedules.

## Verification

- python -m pytest tests apps/srs-spec/tests -q
- python run.py srs --help; python run.py issues --help
- Git ancestry checks for both saved tips; source byte comparison for all original application .py files.
- Ignored data file count/size comparison; staged paths exclude protected files.
- Scheduled-task WorkingDirectory readback and GitHub name/default branch readback.
