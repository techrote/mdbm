# mdbm-config/1.0

Status: normative closed schema. See the fully commented `mdbm.conf` for runnable values.

Precedence is built-in safe defaults → config → CLI → current-session changes. A session restore supplies dynamic defaults only where no explicit CLI override exists. Config is never rewritten by the application. Unknown sections/fields are diagnosed and ignored. Bad values revert individually. TOML parse failure uses safe defaults.

| Section | Fields |
|---|---|
| root | `schema = "mdbm-config/1.0"` |
| application | `root` string, `state_directory` string, `max_file_mib` integer 1–64 |
| layout | `left_visible`, `right_visible`, `edit_visible`, `view_visible` booleans; `left_tile` filesystem/contents; `view_first` boolean; `left_ratio` 0.1–0.9; `view_ratio` 0.1–0.9; `min_width` 8–80; `min_height` 2–20 |
| artspace | `outer` four nonnegative integers [top,right,bottom,left], default [4,4,4,4]; `maximum` four integers 0–40; `border_extra`, `divider_extra`, `spacer_extra` integers 0–8; `band_maximum` 0–12 |
| input | `profile` standard/vim; `tab_size` 1–8; `double_click_ms` 150–700; `submenu_delay_ms` 100–800 |
| bindings | quoted terminal key sequence → registered command ID; unknown, conflicting or reserved keys are diagnosed |
| appearance | `theme` stable ID; `theme_directories` array of directory strings; `immersive` boolean; `seed` integer (−1 means theme policy); `motion` off/reduced/normal/high |
| capabilities | `colour_depth` auto/truecolour/256/16/mono; `unicode` boolean; `mouse` boolean; `terminal_focus` boolean |
| accessibility | `high_contrast`, `reduced_motion`, `ascii_only` booleans |
| scripts | `allowlist` array of sibling `.py` basenames; `max_output_kib` 16–1024 |
| session | `restore` boolean; `recovery_seconds` 1–300; `save_seconds` 1–300; `parse_debounce_ms` 30–2000; `fps` 1–30; `paint_budget` 10000–2000000 |

Paths in application and appearance settings resolve relative to the config directory. The default config is alongside `mdbm.py`. An explicit startup file defines a workspace root from its parent; a startup directory is the root. `--theme` and `--safe-theme` override restored theme selection. `--no-restore` skips both session and recovery.

Key syntax follows prompt_toolkit names, for example `c-s`, `f10`, `escape t`, `s-left`. Space separates a key chord sequence, not simultaneously held keys. The portable Alt representation is `escape <letter>`. Ctrl+P (palette), Ctrl+T (restore), Ctrl+Q (guarded quit), Escape, and F10 cannot be removed. Editing/navigation keys cannot be reassigned to actions which would strand dialogs. All commands remain discoverable through the host palette.

Scripts are a basename allowlist, not globs, paths or commands. Even an allowlisted script needs a user activation and confirmation. Script arguments are entered as JSON, for example `["--count", "4", "a value containing spaces"]`.
