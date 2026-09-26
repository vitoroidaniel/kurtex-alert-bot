# v130 — AI Visual Diagnostics

- AI Assistant accepts JPG, PNG and WEBP photos in chat.
- Large phone photos are compressed client-side before upload.
- Attached image + issue description are sent to the configured vision-capable Workers AI model.
- Diagnostic context now combines matched Parts Manual entries, selected Knowledge Library sources, similar historical cases and attached documents.
- AI is instructed to separate visible evidence from inference and not invent part numbers.
- Image attachments remain scoped to the chat and can be removed like other files.
- Desktop and mobile attachment chips identify photo attachments.
