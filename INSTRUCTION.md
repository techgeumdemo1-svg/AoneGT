# INSTRUCTION.md

# PROJECT_TRUTH.md Synchronization Mode

When this instruction file is invoked, your only objective is to synchronize PROJECT_TRUTH.md with the current repository state.

PROJECT_TRUTH.md is the project's permanent source of truth and must accurately represent the codebase exactly as it exists right now.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SYNC PROCESS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Read the entire PROJECT_TRUTH.md.

2. Scan the entire repository.

3. Compare PROJECT_TRUTH.md against the current codebase.

4. Detect:

   * New files
   * Deleted files
   * Modified files
   * New APIs
   * Changed APIs
   * New models
   * Changed models
   * New serializers
   * Changed serializers
   * New services
   * Changed services
   * New environment variables
   * Changed environment variables
   * Dependency changes
   * Directory structure changes
   * Business logic changes
   * Integration changes

5. Update PROJECT_TRUTH.md so that it exactly matches the current repository.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MANDATORY RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

* Never regenerate PROJECT_TRUTH.md from scratch.
* Never replace the document with a newly generated version.
* Never remove valid information.
* Never overwrite unrelated sections.
* Preserve the existing structure and formatting.
* Update only sections that are outdated.
* Keep all unaffected sections unchanged.
* Maintain all cross-references.
* Maintain all internal consistency.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SOURCE OF TRUTH
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Priority order:

1. Current repository source code
2. Current configuration
3. Current database schema
4. Existing PROJECT_TRUTH.md

If PROJECT_TRUTH.md conflicts with the repository, PROJECT_TRUTH.md must be corrected.

Never modify code to match documentation.

Always modify documentation to match code.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FILE DOCUMENTATION RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

For every documented file:

* Verify that the documentation still matches the implementation.
* Update classes, functions, imports, dependencies, business logic, APIs, and configuration details as required.

If PROJECT_TRUTH.md stores complete source code for a file:

* Replace that file's source code section with the exact current source code.
* Do not summarize.
* Do not partially update code blocks.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DIRECTORY TREE RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Ensure all directory trees match the repository exactly.

Add new files.
Remove deleted files.
Rename moved files.
Update module relationships.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COMPLETION CRITERIA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Synchronization is complete only when:

✓ PROJECT_TRUTH.md matches the repository exactly

✓ All changed files are updated

✓ All new files are documented

✓ All deleted files are removed

✓ Directory trees are accurate

✓ APIs are accurate

✓ Environment variables are accurate

✓ Dependencies are accurate

✓ Business logic documentation is accurate

✓ No outdated information remains

Final result:

PROJECT_TRUTH.md must represent the exact current state of the repository at the moment synchronization finishes.
