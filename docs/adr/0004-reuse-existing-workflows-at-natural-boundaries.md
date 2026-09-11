# Reuse existing workflows only at natural task boundaries

Existing Deck Update handles ordinary PowerPoint objects and straightforward table or chart-property edits through OfficeCLI. It reuses table-fill when the task is substantively complex table population and chart-gen when the task is substantively chart generation from structured data, while retaining responsibility for deck-level intent, page integration, and final visual review; no heavy mandatory orchestration layer will be introduced.
