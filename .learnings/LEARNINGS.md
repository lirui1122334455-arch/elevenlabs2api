# Learnings

## [LRN-20260826-001] correction

**Logged**: 2026-08-26T00:15:01+08:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
Do not infer successful ElevenLabs registration from a saved Outlook account entry.

### Details
The user confirmed the Outlook account was registered manually. Automation successfully read the verification email and obtained its long action URL, but did not complete the verification action; manually opening that URL followed by signing in succeeded. Credentials are persisted before browser registration completes, so their presence in the account list is not proof that email verification or automated registration succeeded.

### Suggested Action
Diagnose registration from phase logs and explicit post-verification state. Require positive verification evidence after opening the action URL, and keep mailbox retrieval failures separate from verification-navigation failures.

### Metadata
- Source: user_feedback
- Related Files: services/auto_register/elevenlabs_server.py, services/auto_register/elevenlabs_assisted/browser_flow.py, services/auto_register/elevenlabs_assisted/mailbox_link.py
- Tags: outlook, elevenlabs, email-verification, diagnosis

### Resolution
- **Resolved**: 2026-08-26T00:34:56+08:00
- **Notes**: Registration now requires a successful Firebase `accounts:update` response for the action code, rejects redirect-only failures, raises on verification timeout, isolates the completed action page, and reloads a disabled sign-in form once. The rebuilt service rejected a live invalid-code probe after the page redirected to sign-in.

---

## [LRN-20260826-002] correction

**Logged**: 2026-08-26T01:00:00+08:00
**Priority**: high
**Status**: resolved
**Area**: frontend

### Summary
Credential and user-owned configuration inputs must not contain hardcoded non-URL example values.

### Details
The user identified owned mail domains displayed as a default-looking placeholder. The same pattern also existed for API keys, JWTs, cookies, SSO tokens, and other editable configuration fields. Even synthetic examples can be mistaken for saved values and can expose deployment-specific data when copied from a live environment.

### Suggested Action
Keep unsaved secret and configuration fields visually empty. Only show a non-secret keep-existing status when the backend confirms a write-only value is configured; URL fields may retain URL guidance.

### Metadata
- Source: user_feedback
- Related Files: frontend/src/features/elevenlabs/elevenlabs-page.tsx, frontend/src/features/settings/settings-page.tsx, frontend/src/features/settings/egress-nodes.tsx, frontend/src/features/accounts/accounts-page.tsx
- Tags: secrets, placeholders, configuration, privacy

### Resolution
- **Resolved**: 2026-08-26T01:00:00+08:00
- **Notes**: Removed non-URL examples from credential and configuration inputs, generalized deployment-specific documentation, and excluded local browser snapshots from source control.

---
