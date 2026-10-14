# TUI polish rubric

"Polish" for a terminal UI is not taste — it is a set of properties you can look at and point to on a
grid of cells. Walk the categories below against the *stable* screens of the scenario and record only
what you can evidence with a coordinate or a verbatim excerpt.

Useful reads while doing this: `capture_screen` for the whole frame, `get_screen_region` for the panel
under review, `get_char` when you need to prove an exact column, `get_line` for single-row chrome.

## Categories

### Layout and geometry
- Columns line up across the panel; nothing floats two cells off its neighbours.
- Box-drawing borders are continuous — corners, tees, and joins (`┌ ┬ ┐ │ ├ ┼ ┤ └ ┴ ┘`) intact, no
  gaps where a header or scrollbar pushes a cell aside.
- Panels fill the width they claim; unused margin is either intentional or a defect worth naming.
- Vertical rhythm: consistent blank-line policy between sections; no orphaned separator rows.

### Truncation and overflow
- Long values (paths, URLs, titles, usernames) are cut deliberately: ellipsis or tail-preserving
  truncation, never a mid-word guillotine.
- **Measure the threshold.** Change one variable (path length, item count) and find where chrome
  disappears or text is chopped; a threshold is a finding, "it truncates" is not.
- Wide characters (CJK, emoji, combining marks) and long unbroken tokens are handled, or visibly not.

### Chrome and status
- Title bar, mode indicator, and key hints are present, correct for the current mode, and stable
  across screens.
- Status messages appear after an action and clear when they stop being true; stale banners are a
  finding.
- Key hints match the keys that actually work. A hint for a key that does nothing is worse than none.

### Focus, selection, and cursor
- The focused pane/field is visually distinct (not by colour alone).
- Selection highlight and its inverse (focused-but-unselected) are distinguishable.
- The cursor is where the next keystroke will land; after mode switches it is redrawn, not stranded
  mid-row.

### Text and tone
- Alignment: numeric columns right-aligned, text left-aligned, headers on the same axis.
- Casing and punctuation consistent within one surface (`Save` vs `save`, `1 item` vs `1 items`).
- Pluralisation and unit formatting correct at 0, 1, and many.
- Emphasis is meaningful: dim/bold/colour carries a distinction, not decoration.
- Terminology matches the product's own vocabulary and the docs.

### Feedback and error states
- Every action yields observable feedback (message, count, mode change, spinner).
- Errors state what failed and what to do next; they are not silent or generic.
- Destructive actions confirm; the confirmation names the object.
- Empty states exist and explain the way out.

### Responsiveness
- At 80x24 — and at the narrowest size the app claims to support — nothing overlaps, nothing is
  unreadable, no panel collapses to zero width.
- Longer content than fits is scrollable, paginated, or visibly clipped with an indicator.

### Locale and portability
- Strings that will be localized assume variable width; layouts that assume ASCII width break.
- Numbers, dates, and paths render correctly under a non-`C` locale, or the scenario pins `LC_ALL=C`
  and says so.

### Anomalies
- Flicker, ghosting, duplicated rows, torn frames, or residual characters from a previous screen.
  Capture the same region twice to distinguish a real artifact from a read artifact.

## Severity

| Severity | Meaning |
|---|---|
| blocker | The app is unusable at this size/state, or the screen lies about state (data loss, false success). |
| major | A flow cannot be completed confidently; the user must guess or work around. |
| minor | Visible defect that a careful user notices; the flow still works. |
| nit | Wording, spacing, or alignment polish with no functional impact. |

## Rules for recording

- One remark per row: location (screen row/col or panel), observation, severity, recommendation.
- Quote the cells you are describing; "the status line looks cramped" is unactionable, "row 22 shows
  `Имя файла для записи:<eline/notes.txt` — a 15-character tail with ~45 blank columns" is a bug report.
- Prefer observations that survive a rerun: if the screen was mid-repaint, take a second read before
  writing it down.
- Attribute correctly: app defects in *Visual polish remarks*, harness limits in *Tooling friction*.
  A defect caused by the harness's emulation (not the app's rendering) is not a polish remark.
