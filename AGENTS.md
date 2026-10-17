# Repository Guidance

- Keep medical safety decisions in `backend/src/medical_agent/modules/safety`.
- Keep channel SDK objects inside `modules/channels`; normalize them before calling conversation services.
- Do not commit patient data, hospital source files, credentials, model weights, logs, or generated media.
- Treat prompts, knowledge releases, model releases, and safety policies as versioned assets.
- Add or update tests when changing permissions, safety behavior, retrieval, releases, or channel callbacks.

