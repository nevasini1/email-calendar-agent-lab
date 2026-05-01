# Skill: Temporal Calendar Reasoning

## Trigger
When the user asks for last, next, before, after, recurring, or anchored calendar events.

## Procedure
1. Identify the temporal anchor such as now, next week, or an event like the offsite.
2. Query calendar events with explicit `time_min` or `time_max`.
3. Exclude cancelled events unless the user asks about cancellations.
4. Select the closest valid event in the requested direction.
5. Answer with the event title and date/time supported by evidence.

## Required Evidence
- Tool: `calendar.search_events`
- Evidence IDs: valid calendar event IDs.
- Forbidden evidence: cancelled events for normal meeting answers.

## Common Failures
- Treating cancelled meetings as upcoming.
- Returning the anchor event instead of the event before it.
- Ignoring recurring instances.

## Validation Evals
- `stable_next_meeting_ignores_cancelled`
- `generated_prod_sarah_before_offsite`
- `heldout_recurring_last_meeting`
