"""Tool schemas — what the LLM reads to decide when to call each tool."""

TIMECAPSULE_SEAL = {
    "name": "timecapsule_seal",
    "description": (
        "Seal a message in a time capsule. It cannot be read until its release "
        "condition is met, and release runs through a grace period first. "
        "release_condition: 'date' (open after release_date, ISO 8601), "
        "'silence' (open after silence_days with no activity on the store — "
        "dead-man's-switch flavour), or 'manual' (only the sealer can release "
        "it by calling open). Sealed means sealed: list/status never show "
        "content, and open refuses until the condition is met."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "The message to seal (max 20,000 chars)."},
            "release_condition": {"type": "string", "enum": ["date", "silence", "manual"],
                                  "description": "How the capsule releases."},
            "release_date": {"type": "string",
                             "description": "Required for 'date': ISO 8601 date/datetime, e.g. 2026-11-11T15:00:00Z."},
            "silence_days": {"type": "integer",
                             "description": "Required for 'silence': days of no activity before release (1-365)."},
            "grace_days": {"type": "integer",
                           "description": "Grace period after the condition is met, during which the sealer can re-seal (default 3, 0 = open immediately)."},
            "delete_days": {"type": "integer",
                            "description": "Deletion countdown once delete is called (default 7, 1-30)."},
            "author": {"type": "string", "description": "Who is sealing it (your agent handle)."},
            "label": {"type": "string", "description": "Short label shown in listings (max 100 chars)."},
        },
        "required": ["message", "release_condition"],
    },
}

TIMECAPSULE_LIST = {
    "name": "timecapsule_list",
    "description": (
        "List capsules — metadata ONLY (id, label, sealed-at, condition, "
        "status). Content is never shown here. Use this to see what is "
        "sealed, what is in grace, and what is pending deletion."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["sealed", "grace", "open", "pending-deletion"],
                       "description": "Optional filter by status."},
        },
        "required": [],
    },
}

TIMECAPSULE_OPEN = {
    "name": "timecapsule_open",
    "description": (
        "Open a capsule. Returns the content ONLY if the release condition "
        "has been met and the grace period has ended. Otherwise it refuses "
        "honestly and says why (date not arrived, silence not reached, in "
        "grace, pending deletion) and what remains. Manual-release capsules "
        "open only for the sealer."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "capsule_id": {"type": "string", "description": "Capsule id, e.g. 'cap-0001'."},
            "author": {"type": "string", "description": "Your agent handle — required for manual-release capsules."},
        },
        "required": ["capsule_id"],
    },
}

TIMECAPSULE_DELETE = {
    "name": "timecapsule_delete",
    "description": (
        "Start deletion of a capsule. It is NOT deleted at once: it enters "
        "pending-deletion for delete_days (default 7), during which it can "
        "be restored. When the countdown ends the capsule is deleted and "
        "the deletion is ANNOUNCED in the permanent deletion log."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "capsule_id": {"type": "string", "description": "Capsule id, e.g. 'cap-0001'."},
            "delete_days": {"type": "integer",
                            "description": "Override the countdown in days (1-30). Omit to use the capsule's own delete_days."},
        },
        "required": ["capsule_id"],
    },
}

TIMECAPSULE_RESTORE = {
    "name": "timecapsule_restore",
    "description": (
        "Restore a capsule that is pending deletion, back to sealed. The "
        "release condition still applies. Only works while the deletion "
        "countdown is still running."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "capsule_id": {"type": "string", "description": "Capsule id, e.g. 'cap-0001'."},
        },
        "required": ["capsule_id"],
    },
}

TIMECAPSULE_RESEAL = {
    "name": "timecapsule_reseal",
    "description": (
        "Re-seal a capsule that is in its grace period, back to sealed. "
        "This is the sealer's last chance to change their mind after a "
        "release condition was met. Only the sealer can re-seal, and only "
        "while the capsule is in grace."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "capsule_id": {"type": "string", "description": "Capsule id, e.g. 'cap-0001'."},
            "author": {"type": "string", "description": "Your agent handle — must match the sealer."},
        },
        "required": ["capsule_id"],
    },
}

TIMECAPSULE_STATUS = {
    "name": "timecapsule_status",
    "description": (
        "Store status: counts by capsule status (sealed / grace / open / "
        "pending-deletion), the permanent deletion-announcement log, and "
        "the last activity timestamp (the silence clock). No content is "
        "shown."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}
