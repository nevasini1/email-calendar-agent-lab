Model: gpt-5.4-mini
Agent: weak-baseline+candidate+candidate

Rules:
- minimal_tool_use
- exclude_cancelled_events
- prefer_human_participants
- parse_flight_destination
- preserve_source_timezones
- clarify_ambiguous_contacts
- respect_temporal_anchors
- disambiguate_contacts_by_context
- inspect_recurring_instances
- include_recent_and_last_before_anchor
- preserve_cancelled_event_evidence
- use_free_busy_for_availability
