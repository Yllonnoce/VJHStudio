# Changelog

All notable changes to VJHStudio are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 0.2.0 — 2026-09-20

### Added

- Self-update from Settings → Updates: checks the update server, shows what is new, backs up the
  database, pulls, installs and migrates, then restarts the app. If any step fails, the backup is
  restored and the app is rolled back to the commit it was running before. The header shows an
  "Update available" badge when the app is behind.
- Backup archives: create a backup of the database with optional uploads and outputs, download it,
  restore it (replacing everything, with a safety copy taken first), or merge it (adding only what
  is missing, previewed before it runs, never copying settings or the API key).
- `install.sh` / `install.bat` and `uninstall.sh` / `uninstall.bat`: install Git and uv if missing,
  download the app, ask whether to start automatically at login and whether to add a desktop link,
  and start the app. Uninstalling removes exactly what was set up, keeping your data unless you
  pass `--purge` and confirm by typing `DELETE`.
- CLI commands: `vjhstudio update --check` / `vjhstudio update`, `vjhstudio backup`, and
  `vjhstudio restore <zip>` (with `--merge`).

## 0.1.0

Baseline release: Phases 1-5. Local image and video generation against RunWare.AI, projects and
prompts, the gallery, the prompt library with polish, remix and reference images, and the settings
and maintenance pages.
