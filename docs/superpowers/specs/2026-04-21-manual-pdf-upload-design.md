# Manual PDF Upload Design

## Goal
Add a manual upload path for papers that do not have a usable PDF yet. The upload should live on the paper detail page, store the uploaded file as the paper's primary PDF artifact, and enqueue parsing immediately.

## Scope
- Add a PDF upload form to the paper detail page.
- Add a POST endpoint to accept a single uploaded PDF for a paper.
- Persist the file using the existing artifact storage adapter.
- Insert a matching `artifacts` row for the uploaded file.
- Update the paper to point at the uploaded PDF and mark it resolved.
- Enqueue `parse_artifact` for the new artifact.
- Add a GET endpoint to serve the stored artifact back to the browser.

Out of scope:
- Reworking automatic resolution.
- Bulk upload.
- Non-PDF file types.
- A separate admin console.

## User Flow
1. User opens a paper detail page.
2. If the paper is unresolved, the page shows a manual PDF upload form.
3. User uploads a PDF.
4. The server stores the file, records the artifact, updates the paper's `best_pdf_url`, and enqueues parsing.
5. The page reloads and shows the uploaded PDF as the primary PDF link.

## Backend Design
Add a small upload service at the web layer rather than extending the resolver job. The route handler will:
- validate the incoming upload is present and looks like a PDF,
- write the bytes using the configured artifact storage adapter,
- create an artifact record tied to the paper,
- update the paper's `best_pdf_url` and `resolution_status` while leaving `best_landing_url` unchanged,
- enqueue `parse_artifact` using the new artifact's storage URI.

The artifact download route will read the stored file back through the storage adapter and stream it to the client.

The stored PDF should be exposed through a dedicated app URL, and that URL becomes the paper's `best_pdf_url`.

## Data Model
No schema migration is required if the existing `artifacts` table fields are reused:
- `paper_id`
- `artifact_kind`
- `label`
- `resolution_reason`
- `storage_uri`
- `storage_key`
- `mime_type`
- `download_status`

The upload path should create a PDF artifact row with a clear label such as `manual upload` and a resolution reason such as `manual_pdf_upload`.

## Error Handling
- Reject missing files and non-PDF uploads with a user-facing error.
- If storage write fails, do not insert database rows.
- If DB update fails after storage write, return an error and leave the stored file orphaned for later cleanup.
- If parse enqueue fails, keep the upload persisted and show an error so the user can retry parsing later.

## URLs
Use a dedicated app route for stored artifact access so the detail page can link to a stable local URL instead of the raw storage URI.

## Tests
- Route test for uploading a PDF and redirecting back to the paper detail page.
- Route test for rejecting non-PDF uploads.
- Repository or service test for the paper update and artifact insert behavior.
- Route test for streaming an uploaded artifact.

## Acceptance Criteria
- A user can upload a PDF from a paper detail page.
- The uploaded PDF becomes the paper's primary PDF link.
- The system stores artifact metadata and queues parsing automatically.
- Existing resolution and parsing flows continue to work unchanged.
