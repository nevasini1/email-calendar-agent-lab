Model: gpt-5.4-mini
Agent: weak-baseline+candidate

Rules:
- minimal_tool_use
- exclude_cancelled_events
- prefer_human_participants
- parse_flight_destination
- preserve_source_timezones
- clarify_ambiguous_contacts
- respect_temporal_anchors
